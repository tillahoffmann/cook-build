from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class TaskRecord:
    task_id: str
    digest: str
    last_started: datetime | None = None
    last_succeeded: datetime | None = None
    last_failed: datetime | None = None
    error: str | None = None

    @property
    def duration(self) -> float | None:
        if self.last_started is None:
            return None
        # Use whichever of succeeded/failed is most recent
        candidates = [
            t for t in (self.last_succeeded, self.last_failed) if t is not None
        ]
        if not candidates:
            return None
        end = max(candidates)
        if end < self.last_started:
            return None
        return (end - self.last_started).total_seconds()


class FileDigestCache:
    """Cache file content hashes keyed by (resolved_path, mtime_ns).

    If a file's mtime hasn't changed since the last hash, the cached
    digest is returned without re-reading the file.
    """

    def __init__(self) -> None:
        self._cache: dict[Path, tuple[int, bytes]] = {}

    def hash_file(self, path: Path) -> bytes:
        resolved = path.resolve()
        mtime_ns = resolved.stat().st_mtime_ns
        cached = self._cache.get(resolved)
        if cached is not None and cached[0] == mtime_ns:
            return cached[1]
        data = resolved.read_bytes()
        content_hash = hashlib.sha256(data).digest()
        self._cache[resolved] = (mtime_ns, content_hash)
        return content_hash


class BuildStore(ABC):
    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None: ...

    @abstractmethod
    def save(self, record: TaskRecord) -> None: ...

    @abstractmethod
    def delete(self, task_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
