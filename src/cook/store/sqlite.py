from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from . import BuildStore, TaskRecord


class SqliteBuildStore(BuildStore):
    def __init__(self, path: str | Path = ".cook.db") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                last_started TEXT,
                last_succeeded TEXT,
                last_failed TEXT,
                error TEXT
            )
            """
        )
        self._conn.commit()

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._conn.execute(
            "SELECT task_id, digest, last_started, last_succeeded, last_failed, "
            "error FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return TaskRecord(
            task_id=row[0],
            digest=row[1],
            last_started=_parse_dt(row[2]),
            last_succeeded=_parse_dt(row[3]),
            last_failed=_parse_dt(row[4]),
            error=row[5],
        )

    def save(self, record: TaskRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO tasks (task_id, digest, last_started, last_succeeded,
                               last_failed, error)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                digest=excluded.digest,
                last_started=COALESCE(excluded.last_started, tasks.last_started),
                last_succeeded=COALESCE(excluded.last_succeeded, tasks.last_succeeded),
                last_failed=COALESCE(excluded.last_failed, tasks.last_failed),
                error=excluded.error
            """,
            (
                record.task_id,
                record.digest,
                _format_dt(record.last_started),
                _format_dt(record.last_succeeded),
                _format_dt(record.last_failed),
                record.error,
            ),
        )
        self._conn.commit()

    def delete(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteBuildStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()


def _parse_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value)


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()
