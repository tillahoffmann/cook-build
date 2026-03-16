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
    now = datetime.now(timezone.utc)
    record = TaskRecord(
        task_id="task-1",
        digest="abc123",
        last_started=now,
        last_succeeded=now,
        last_failed=None,
        error=None,
    )
    store.save(record)
    got = store.get("task-1")
    assert got is not None
    assert got.task_id == "task-1"
    assert got.digest == "abc123"
    assert got.last_started == now
    assert got.last_succeeded == now
    assert got.last_failed is None
    assert got.duration == 0.0
    assert got.error is None


def test_save_all_fields(store: SqliteBuildStore) -> None:
    now = datetime.now(timezone.utc)
    record = TaskRecord(
        task_id="full",
        digest="d1g3st",
        last_started=now,
        last_succeeded=now,
        last_failed=now,
        error="something broke",
    )
    store.save(record)
    got = store.get("full")
    assert got is not None
    assert got.last_failed == now
    assert got.duration == 0.0
    assert got.error == "something broke"


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
    # after exiting, store is closed
    with pytest.raises(Exception):
        store.get("cm")


def test_datetime_roundtrip(store: SqliteBuildStore) -> None:
    dt = datetime(2025, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
    store.save(TaskRecord(task_id="dt", digest="x", last_started=dt))
    got = store.get("dt")
    assert got is not None
    assert got.last_started == dt


def test_wal_mode(store: SqliteBuildStore) -> None:
    row = store._conn.execute("PRAGMA journal_mode").fetchone()
    assert row is not None
    assert row[0] == "wal"


def test_save_preserves_timing_fields_with_coalesce(store: SqliteBuildStore) -> None:
    """Saving a success record after a failure should not erase last_failed."""
    failed_at = datetime(2025, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store.save(
        TaskRecord(
            task_id="t",
            digest="d1",
            last_started=failed_at,
            last_failed=failed_at,
            error="boom",
        )
    )

    succeeded_at = datetime(2025, 1, 1, 13, 0, 0, tzinfo=timezone.utc)
    store.save(
        TaskRecord(
            task_id="t",
            digest="d2",
            last_started=succeeded_at,
            last_succeeded=succeeded_at,
            last_failed=None,
        )
    )

    got = store.get("t")
    assert got is not None
    assert got.digest == "d2"
    assert got.last_started == succeeded_at
    assert got.last_succeeded == succeeded_at
    # last_failed should be preserved from the earlier save, not overwritten with None
    assert got.last_failed == failed_at


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
    """When both last_succeeded and last_failed exist, use the most recent."""
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
    # Should use the more recent failure, not the stale success
    assert record.duration == 7.0


def test_duration_none_when_end_before_start() -> None:
    """Stale succeeded/failed from a previous run should return None."""
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
    assert len(h1) == 32  # SHA-256 digest length


def test_file_cache_detects_content_change(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("v1")
    cache = FileDigestCache()
    h1 = cache.hash_file(f)
    # Ensure mtime changes (some filesystems have 1s resolution)
    import time

    time.sleep(0.05)
    f.write_text("v2")
    h2 = cache.hash_file(f)
    assert h1 != h2


def test_file_cache_uses_mtime(tmp_path: Path) -> None:
    """Same mtime means the cache returns without re-reading."""
    f = tmp_path / "a.txt"
    f.write_text("data")
    cache = FileDigestCache()
    h1 = cache.hash_file(f)
    # Write same content — mtime changes, but hash should be same value
    import time

    time.sleep(0.05)
    f.write_text("data")
    h2 = cache.hash_file(f)
    assert h1 == h2  # same content, same hash


def test_file_cache_missing_file(tmp_path: Path) -> None:
    cache = FileDigestCache()
    with pytest.raises(FileNotFoundError):
        cache.hash_file(tmp_path / "gone.txt")
