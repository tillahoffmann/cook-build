from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cook.store import BuildStore, FileDigestCache, TaskRecord
from cook.store.sqlite import SqliteBuildStore


@pytest.fixture
def store(tmp_path: Path) -> Generator[SqliteBuildStore]:
    with SqliteBuildStore(tmp_path / "test.db") as s:
        yield s


def test_get_unknown_returns_none(store: SqliteBuildStore) -> None:
    assert store.get("nonexistent") is None


def test_save_and_get_roundtrip(store: SqliteBuildStore) -> None:
    store.save(TaskRecord(task_id="task-1", digest="abc123"))
    got = store.get("task-1")
    assert got is not None
    assert got.task_id == "task-1"
    assert got.digest == "abc123"


def test_start_and_finish_run_succeeded(store: SqliteBuildStore) -> None:
    started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2025, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    run_id = store.start_run("t", "session-1", 1234, started)
    assert run_id > 0

    store.finish_run(run_id, "succeeded", finished, digest="abc")
    got = store.get("t")
    assert got is not None
    assert got.digest == "abc"
    assert got.last_started == started
    assert got.last_succeeded == finished
    assert got.last_failed is None
    assert got.duration == 5.0


def test_start_and_finish_run_failed(store: SqliteBuildStore) -> None:
    started = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    finished = datetime(2025, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
    run_id = store.start_run("t", "session-1", 1234, started)
    store.finish_run(run_id, "failed", finished, error="boom")

    got = store.get("t")
    assert got is not None
    assert got.last_failed == finished
    assert got.error == "boom"


def test_get_preserves_history_across_runs(store: SqliteBuildStore) -> None:
    """A success after a failure should preserve last_failed."""
    t1 = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc)

    # First run: failure
    r1 = store.start_run("t", "s1", 1, t1)
    store.finish_run(r1, "failed", t1, error="boom")

    # Second run: success
    r2 = store.start_run("t", "s2", 1, t2)
    store.finish_run(r2, "succeeded", t2, digest="d2")

    got = store.get("t")
    assert got is not None
    assert got.digest == "d2"
    assert got.last_succeeded == t2
    assert got.last_failed == t1  # preserved from earlier run


def test_save_upserts(store: SqliteBuildStore) -> None:
    store.save(TaskRecord(task_id="t", digest="v1"))
    store.save(TaskRecord(task_id="t", digest="v2"))
    got = store.get("t")
    assert got is not None
    assert got.digest == "v2"


def test_delete_removes_record(store: SqliteBuildStore) -> None:
    store.save(TaskRecord(task_id="t", digest="d"))
    store.delete("t")
    assert store.get("t") is None


def test_delete_nonexistent_is_noop(store: SqliteBuildStore) -> None:
    store.delete("ghost")  # should not raise


def test_close_then_operations_fail(tmp_path: Path) -> None:
    store = SqliteBuildStore(tmp_path / "test.db")
    store.close()
    with pytest.raises(Exception):
        store.get("anything")


def test_context_manager(tmp_path: Path) -> None:
    with SqliteBuildStore(tmp_path / "test.db") as store:
        store.save(TaskRecord(task_id="cm", digest="d"))
        assert store.get("cm") is not None
    with pytest.raises(Exception):
        store.get("cm")


def test_wal_mode(store: SqliteBuildStore) -> None:
    row = store._conn.execute("PRAGMA journal_mode").fetchone()
    assert row is not None
    assert row[0] == "wal"


def test_get_running(store: SqliteBuildStore) -> None:
    import os

    now = datetime.now(timezone.utc)
    run_id = store.start_run("t", "s1", os.getpid(), now)
    running = store.get_running("t")
    assert running is not None
    assert running.id == run_id
    assert running.status == "pending"
    assert running.is_alive  # our own PID

    store.finish_run(run_id, "succeeded", now, digest="d")
    assert store.get_running("t") is None


def test_get_running_dead_pid(store: SqliteBuildStore) -> None:
    now = datetime.now(timezone.utc)
    store.start_run("t", "s1", 999999, now)  # unlikely to be alive
    running = store.get_running("t")
    assert running is not None
    assert not running.is_alive


def test_cleanup_session(store: SqliteBuildStore) -> None:
    now = datetime.now(timezone.utc)
    # a is pending (never started)
    store.start_run("a", "session-x", 1, now)
    # b is running (started executing)
    b_id = store.start_run("b", "session-x", 1, now)
    store.update_run_status(b_id, "running")
    # c is in a different session
    store.start_run("c", "session-y", 1, now)

    store.cleanup_session("session-x")

    # a was pending — should be deleted entirely (not failed)
    assert store.get_running("a") is None
    assert store.get("a") is None  # no record at all
    # b was running — should be marked as failed
    assert store.get_running("b") is None
    record_b = store.get("b")
    assert record_b is not None
    assert record_b.error == "interrupted"
    # c should still be pending (different session)
    assert store.get_running("c") is not None


def test_check_constraint(store: SqliteBuildStore) -> None:
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO runs (task_id, session_id, pid, status, started_at) "
            "VALUES ('t', 's', 1, 'invalid', '2025-01-01T00:00:00')"
        )


# --- TaskRecord duration tests ---


def test_duration_derived_from_timestamps() -> None:
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    end = datetime(2025, 1, 1, 12, 0, 5, tzinfo=timezone.utc)
    record = TaskRecord(task_id="t", digest="d", last_started=start, last_succeeded=end)
    assert record.duration == 5.0

    failed_record = TaskRecord(
        task_id="t", digest="d", last_started=start, last_failed=end
    )
    assert failed_record.duration == 5.0


def test_duration_uses_most_recent_end() -> None:
    start = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    old_success = datetime(2025, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
    new_failure = datetime(2025, 1, 1, 12, 0, 7, tzinfo=timezone.utc)
    record = TaskRecord(
        task_id="t",
        digest="d",
        last_started=start,
        last_succeeded=old_success,
        last_failed=new_failure,
    )
    assert record.duration == 7.0


def test_duration_none_when_end_before_start() -> None:
    start = datetime(2025, 1, 1, 12, 0, 10, tzinfo=timezone.utc)
    old_success = datetime(2025, 1, 1, 12, 0, 3, tzinfo=timezone.utc)
    record = TaskRecord(
        task_id="t",
        digest="d",
        last_started=start,
        last_succeeded=old_success,
    )
    assert record.duration is None


def test_duration_none_without_timestamps() -> None:
    assert TaskRecord(task_id="t", digest="d").duration is None
    assert (
        TaskRecord(
            task_id="t", digest="d", last_started=datetime.now(timezone.utc)
        ).duration
        is None
    )


def test_build_store_is_abc() -> None:
    with pytest.raises(TypeError):
        BuildStore()  # type: ignore[abstract]


# --- FileDigestCache tests ---


def test_file_cache_returns_consistent_hash(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello")
    cache = FileDigestCache()
    h1 = cache.hash_file(f)
    h2 = cache.hash_file(f)
    assert h1 == h2
    assert isinstance(h1, bytes)
    assert len(h1) == 32


def test_file_cache_detects_content_change(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("v1")
    cache = FileDigestCache()
    h1 = cache.hash_file(f)
    import time

    time.sleep(0.05)
    f.write_text("v2")
    h2 = cache.hash_file(f)
    assert h1 != h2


def test_file_cache_uses_mtime(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("data")
    cache = FileDigestCache()
    h1 = cache.hash_file(f)
    import time

    time.sleep(0.05)
    f.write_text("data")
    h2 = cache.hash_file(f)
    assert h1 == h2


def test_file_cache_missing_file(tmp_path: Path) -> None:
    cache = FileDigestCache()
    with pytest.raises(FileNotFoundError):
        cache.hash_file(tmp_path / "gone.txt")


def test_hash_resource_caches_gcs_resources(gcs_bucket) -> None:  # type: ignore[no-untyped-def]
    """hash_resource should cache digest results for GCS resources."""
    import hashlib

    from cook.resource import GcsResource

    gcs_bucket.blob("cached.txt").upload_from_string(b"cache me")

    cache = FileDigestCache()
    resource = GcsResource(bucket=gcs_bucket.name, object_key="cached.txt")

    h1 = cache.hash_resource(resource)
    h2 = cache.hash_resource(resource)
    assert h1 == h2 == hashlib.md5(b"cache me").digest()
    assert resource.label in cache._remote_cache  # cached
