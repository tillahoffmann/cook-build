from __future__ import annotations

import asyncio
from abc import ABC
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, overload

_E = TypeVar("_E", bound="Executor")

from ..task import Task


class TaskExecutionError(Exception):
    def __init__(self, task: Task, returncode: int, stderr: str) -> None:
        self.task = task
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(
            f"Task {task.name!r} failed with return code {returncode}:\n{stderr}"
        )


Handler = Callable[["Executor", Task], Awaitable[None]]


class Executor(ABC):
    _handlers: dict[type[Task], Handler] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Each subclass gets its own copy of the parent's handlers
        cls._handlers = dict(cls._handlers)

    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @classmethod
    def from_config(
        cls, executor_config: dict[str, Any], jobs: int | None = None
    ) -> Executor:
        raise NotImplementedError

    @overload
    @classmethod
    def register_handler(
        cls, handler: Handler, *, task_type: type[Task]
    ) -> Handler: ...

    @overload
    @classmethod
    def register_handler(
        cls, *, task_type: type[Task]
    ) -> Callable[[Handler], Handler]: ...

    @classmethod
    def register_handler(
        cls,
        handler: Handler | None = None,
        *,
        task_type: type[Task],
    ) -> Handler | Callable[[Handler], Handler]:
        if handler is None:

            def decorator(fn: Handler) -> Handler:
                cls._handlers[task_type] = fn
                return fn

            return decorator
        cls._handlers[task_type] = handler
        return handler

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


_executor_registry: dict[str, tuple[type[Executor], type[Any] | None]] = {}


def register_executor(
    name: str,
    config_cls: type[Any] | None = None,
) -> Callable[[type[_E]], type[_E]]:
    def decorator(cls: type[_E]) -> type[_E]:
        _executor_registry[name] = (cls, config_cls)
        return cls

    return decorator


def get_executor(name: str) -> type[Executor]:
    if name not in _executor_registry:
        available = ", ".join(sorted(_executor_registry)) or "(none)"
        raise ValueError(f"Unknown executor {name!r}. Available: {available}")
    return _executor_registry[name][0]


from .local import LocalExecutor
from .slurm import SlurmExecutor

__all__ = [
    "Executor",
    "LocalExecutor",
    "SlurmExecutor",
    "TaskExecutionError",
    "get_executor",
    "register_executor",
]
