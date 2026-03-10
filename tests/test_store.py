from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cook.store import BuildStore, TaskRecord
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
        duration=1.5,
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
    assert got.duration == 1.5
    assert got.error is None


def test_save_all_fields(store: SqliteBuildStore) -> None:
    now = datetime.now(timezone.utc)
    record = TaskRecord(
        task_id="full",
        digest="d1g3st",
        last_started=now,
        last_succeeded=now,
        last_failed=now,
        duration=3.14,
        error="something broke",
    )
    store.save(record)
    got = store.get("full")
    assert got is not None
    assert got.last_failed == now
    assert got.duration == 3.14
    assert got.error == "something broke"


def test_save_upserts(store: SqliteBuildStore) -> None:
    store.save(TaskRecord(task_id="t", digest="v1"))
    store.save(TaskRecord(task_id="t", digest="v2", duration=2.0))
    got = store.get("t")
    assert got is not None
    assert got.digest == "v2"
    assert got.duration == 2.0


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


def test_build_store_is_abc() -> None:
    with pytest.raises(TypeError):
        BuildStore()  # type: ignore[abstract]
