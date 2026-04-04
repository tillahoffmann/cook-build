from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

from . import BuildStore, RunRecord, TaskRecord


class SqliteBuildStore(BuildStore):
    def __init__(self, path: str | Path = ".cook/store.db") -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(p))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed')),
                started_at TEXT NOT NULL,
                finished_at TEXT,
                digest TEXT,
                error TEXT
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_task_status
            ON runs(task_id, status, id DESC)
            """
        )
        self._conn.commit()

    def get(self, task_id: str) -> TaskRecord | None:
        row = self._conn.execute(
            """
            SELECT
                (SELECT started_at FROM runs
                 WHERE task_id = ? AND status IN ('running', 'succeeded', 'failed')
                 ORDER BY id DESC LIMIT 1),
                (SELECT digest FROM runs
                 WHERE task_id = ? AND status = 'succeeded'
                 ORDER BY id DESC LIMIT 1),
                (SELECT finished_at FROM runs
                 WHERE task_id = ? AND status = 'succeeded'
                 ORDER BY id DESC LIMIT 1),
                (SELECT finished_at FROM runs
                 WHERE task_id = ? AND status = 'failed'
                 ORDER BY id DESC LIMIT 1),
                (SELECT error FROM runs
                 WHERE task_id = ? AND status = 'failed'
                 ORDER BY id DESC LIMIT 1)
            FROM runs WHERE task_id = ?
            """,
            (task_id, task_id, task_id, task_id, task_id, task_id),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return TaskRecord(
            task_id=task_id,
            digest=row[1],
            last_started=_parse_dt(row[0]),
            last_succeeded=_parse_dt(row[2]),
            last_failed=_parse_dt(row[3]),
            error=row[4],
        )

    def start_run(
        self, task_id: str, session_id: str, pid: int, started_at: datetime
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO runs (task_id, session_id, pid, status, started_at) "
            "VALUES (?, ?, ?, 'pending', ?)",
            (task_id, session_id, pid, _format_dt(started_at)),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def update_run_status(self, run_id: int, status: str) -> None:
        self._conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        self._conn.commit()

    def finish_run(
        self,
        run_id: int,
        status: str,
        finished_at: datetime,
        digest: str | None = None,
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, digest = ?, error = ? "
            "WHERE id = ?",
            (status, _format_dt(finished_at), digest, error, run_id),
        )
        self._conn.commit()

    def get_running(self, task_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT id, task_id, session_id, pid, status, started_at, "
            "finished_at, digest, error "
            "FROM runs WHERE task_id = ? AND status IN ('pending', 'running') "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        started_at = _parse_dt(row[5])
        assert started_at is not None
        return RunRecord(
            id=row[0],
            task_id=row[1],
            session_id=row[2],
            pid=row[3],
            status=row[4],
            started_at=started_at,
            finished_at=_parse_dt(row[6]),
            digest=row[7],
            error=row[8],
        )

    def delete_run(self, run_id: int) -> None:
        self._conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        self._conn.commit()

    def cleanup_session(self, session_id: str) -> None:
        # Pending tasks were never started — just remove them
        self._conn.execute(
            "DELETE FROM runs WHERE session_id = ? AND status = 'pending'",
            (session_id,),
        )
        # Running tasks were interrupted — mark as failed
        self._conn.execute(
            "UPDATE runs SET status = 'failed', finished_at = ?, "
            "error = 'interrupted' "
            "WHERE session_id = ? AND status = 'running'",
            (_format_dt(datetime.now(timezone.utc)), session_id),
        )
        self._conn.commit()

    def delete(self, task_id: str) -> None:
        self._conn.execute("DELETE FROM runs WHERE task_id = ?", (task_id,))
        self._conn.commit()

    def save(self, record: TaskRecord) -> None:
        """Save a task record directly (used by validate command).

        Inserts a synthetic 'succeeded' run with the given digest.
        """
        self._conn.execute(
            "INSERT INTO runs (task_id, session_id, pid, status, started_at, "
            "finished_at, digest) VALUES (?, 'validate', 0, 'succeeded', ?, ?, ?)",
            (
                record.task_id,
                _format_dt(record.last_started or datetime.now(timezone.utc)),
                _format_dt(record.last_succeeded or datetime.now(timezone.utc)),
                record.digest,
            ),
        )
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
    if value is None:  # pragma: no cover
        return None
    return value.isoformat()
