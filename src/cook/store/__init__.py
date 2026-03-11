from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


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
        end = self.last_succeeded or self.last_failed
        if end is None:
            return None
        return (end - self.last_started).total_seconds()


class BuildStore(ABC):
    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None: ...

    @abstractmethod
    def save(self, record: TaskRecord) -> None: ...

    @abstractmethod
    def delete(self, task_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
