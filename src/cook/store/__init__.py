from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..resource import FileResource, Resource


@dataclass
class TaskRecord:
    """Derived view of a task's current state from its run history."""

    task_id: str
    digest: str | None = None
    last_started: datetime | None = None
    last_succeeded: datetime | None = None
    last_failed: datetime | None = None
    error: str | None = None

    @property
    def duration(self) -> float | None:
        if self.last_started is None:
            return None
        candidates = [
            t for t in (self.last_succeeded, self.last_failed) if t is not None
        ]
        if not candidates:
            return None
        end = max(candidates)
        if end < self.last_started:
            return None
        return (end - self.last_started).total_seconds()


@dataclass
class RunRecord:
    """A single run of a task."""

    id: int
    task_id: str
    session_id: str
    pid: int
    status: str  # 'pending', 'running', 'succeeded', 'failed'
    started_at: datetime
    finished_at: datetime | None = None
    digest: str | None = None
    error: str | None = None

    @property
    def is_alive(self) -> bool:
        """Check if the process that started this run is still alive."""
        try:
            os.kill(self.pid, 0)
            return True
        except PermissionError:  # pragma: no cover
            return True  # process exists but we can't signal it
        except ProcessLookupError:
            return False


class FileDigestCache:
    """Cache file content hashes keyed by (resolved_path, mtime_ns)."""

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[int, bytes]] = {}
        self._remote_cache: dict[str, bytes] = {}

    def hash_file(self, path: Path) -> bytes:
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
        cached = self._cache.get(resolved)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        h = hashlib.sha256()
        with resolved.open("rb") as f:
            while block := f.read(1024 * 1024):
                h.update(block)
        content_hash = h.digest()
        self._cache[resolved] = (mtime_ns, content_hash)
        return content_hash

    def hash_resource(self, resource: Resource) -> bytes:
        if isinstance(resource, FileResource):
            return self.hash_file(resource.path)
        label = resource.label
        cached = self._remote_cache.get(label)
        if cached is not None:
            return cached
        digest = resource.digest()
        self._remote_cache[label] = digest
        return digest


class BuildStore(ABC):
    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None:
        """Get the current state of a task (derived from run history)."""
        ...

    @abstractmethod
    def start_run(
        self, task_id: str, session_id: str, pid: int, started_at: datetime
    ) -> int:
        """Record that a task has started running. Returns the run ID."""
        ...

    @abstractmethod
    def update_run_status(self, run_id: int, status: str) -> None:
        """Update a run's status (e.g. pending → running)."""
        ...

    @abstractmethod
    def finish_run(
        self,
        run_id: int,
        status: str,
        finished_at: datetime,
        digest: str | None = None,
        error: str | None = None,
    ) -> None:
        """Mark a run as succeeded or failed."""
        ...

    @abstractmethod
    def get_running(self, task_id: str) -> RunRecord | None:
        """Get the most recent running record for a task, if any."""
        ...

    @abstractmethod
    def delete_run(self, run_id: int) -> None:
        """Delete a single run record (e.g. discard a pending run for a fresh task)."""
        ...

    @abstractmethod
    def cleanup_session(self, session_id: str) -> None:
        """Mark all running tasks for this session as failed."""
        ...

    @abstractmethod
    def delete(self, task_id: str) -> None:
        """Delete all runs for a task (invalidation)."""
        ...

    @abstractmethod
    def save(self, record: TaskRecord) -> None:
        """Save a task record directly (for validate command)."""
        ...

    @abstractmethod
    def close(self) -> None: ...
