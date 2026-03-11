from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Self

from .task import ShellTask, Task
from .transform import DEFAULT_TRANSFORMS, GraphTransform

_active_context: ContextVar[Context | None] = ContextVar(
    "_active_context", default=None
)

_default_context: Context | None = None


class Context:
    def __init__(
        self,
        transforms: list[GraphTransform] | None = None,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self._token: Token[Context | None] | None = None
        self._transforms = transforms if transforms is not None else DEFAULT_TRANSFORMS

    def register(self, task: Task) -> Task:
        if task.name in self._tasks:
            raise ValueError(
                f"Duplicate task name {task.name!r}. Each task must have a unique name."
            )
        self._tasks[task.name] = task
        return task

    def sh(
        self,
        name: str,
        cmd: str,
        *,
        inputs: Sequence[str | Path | Task] | None = None,
        outputs: Sequence[str | Path] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ShellTask:
        task = ShellTask(
            name=name,
            cmd=cmd,
            inputs=list(inputs) if inputs is not None else [],
            outputs=list(outputs) if outputs is not None else [],
            env=env,
            cwd=cwd,
        )
        self.register(task)
        return task

    @property
    def tasks(self) -> dict[str, Task]:
        return dict(self._tasks)

    def __enter__(self) -> Self:
        self._token = _active_context.set(self)
        return self

    def __exit__(self, *args: object) -> None:
        if self._token is not None:
            _active_context.reset(self._token)
            self._token = None

    def validate(self) -> None:
        tasks = self._tasks
        for transform in self._transforms:
            tasks = transform(tasks)
        self._tasks = tasks


def get_context() -> Context:
    ctx = _active_context.get()
    if ctx is not None:
        return ctx
    global _default_context
    if _default_context is None:
        _default_context = Context()
    return _default_context
