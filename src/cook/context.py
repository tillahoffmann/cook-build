from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Self

from cook.task import ShellTask, Task

_active_context: ContextVar[Context | None] = ContextVar(
    "_active_context", default=None
)

_default_context: Context | None = None

_GLOB_CHARS = set("*?[]")


class Context:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._token: Token[Context | None] | None = None

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
        # 1. Check glob characters in task names (belt-and-suspenders)
        for name in self._tasks:
            if any(c in _GLOB_CHARS for c in name):
                raise ValueError(
                    f"Task name {name!r} contains glob characters (*?[]). "
                    "Task names must not contain glob characters."
                )

        # 2. All transitive dependencies are registered
        for task in self._tasks.values():
            for dep in task.task_deps:
                if dep.name not in self._tasks:
                    raise ValueError(
                        f"Task {task.name!r} depends on {dep.name!r}, "
                        "which is not registered in this context."
                    )

        # 3. No duplicate output paths across tasks
        seen_outputs: dict[Path, str] = {}
        for task in self._tasks.values():
            for out in task.outputs:
                resolved = Path(out).resolve()
                if resolved in seen_outputs:
                    raise ValueError(
                        f"Duplicate output path {str(out)!r}: "
                        f"both {seen_outputs[resolved]!r} and {task.name!r} "
                        "declare it as an output."
                    )
                seen_outputs[resolved] = task.name

        # 4. No task's file inputs overlap with its own outputs
        for task in self._tasks.values():
            resolved_outputs = {Path(o).resolve() for o in task.outputs}
            for inp in task.file_inputs:
                resolved_inp = Path(inp).resolve()
                if resolved_inp in resolved_outputs:
                    raise ValueError(
                        f"Task {task.name!r} has {str(inp)!r} as both "
                        "an input and an output."
                    )

        # 5. No cycles (DFS-based)
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {name: WHITE for name in self._tasks}

        def dfs(name: str, path: list[str]) -> None:
            color[name] = GRAY
            path.append(name)
            for dep in self._tasks[name].task_deps:
                if color[dep.name] == GRAY:
                    cycle_start = path.index(dep.name)
                    cycle = path[cycle_start:] + [dep.name]
                    raise ValueError(f"Dependency cycle detected: {' -> '.join(cycle)}")
                if color[dep.name] == WHITE:
                    dfs(dep.name, path)
            path.pop()
            color[name] = BLACK

        for name in self._tasks:
            if color[name] == WHITE:
                dfs(name, [])


def get_context() -> Context:
    ctx = _active_context.get()
    if ctx is not None:
        return ctx
    global _default_context
    if _default_context is None:
        _default_context = Context()
    return _default_context
