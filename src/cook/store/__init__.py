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
    duration: float | None = None
    error: str | None = None


class BuildStore(ABC):
    @abstractmethod
    def get(self, task_id: str) -> TaskRecord | None: ...

    @abstractmethod
    def save(self, record: TaskRecord) -> None: ...

    @abstractmethod
    def delete(self, task_id: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
