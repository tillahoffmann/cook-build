from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from cook.task import Task


class TaskExecutionError(Exception):
    def __init__(self, task: Task, returncode: int, stderr: str) -> None:
        self.task = task
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Task {task.name!r} failed with return code {returncode}:\n{stderr}"
        )


class Executor(ABC):
    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._handlers: dict[
            type[Task], Callable[[Executor, Task], Awaitable[None]]
        ] = {}

    def register_handler(
        self,
        task_type: type[Task],
        handler: Callable[[Executor, Task], Awaitable[None]],
    ) -> None:
        self._handlers[task_type] = handler

    def _resolve_handler(
        self, task_type: type[Task]
    ) -> Callable[[Executor, Task], Awaitable[None]]:
        for cls in task_type.__mro__:
            if cls in self._handlers:
                return self._handlers[cls]
        raise TypeError(
            f"No handler registered for task type {task_type.__name__!r} "
            f"or any of its base classes"
        )

    async def execute(self, task: Task) -> None:
        handler = self._resolve_handler(type(task))
        async with self._semaphore:
            await handler(self, task)


from cook.executor.local import LocalExecutor

__all__ = ["Executor", "LocalExecutor", "TaskExecutionError"]
