from __future__ import annotations

import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Self

from .resource import FileResource, Resource, resolve_resource
from .task import GroupTask, ShellTask, Task
from .transform import DEFAULT_TRANSFORMS, GraphTransform

_COOK_PACKAGE = str(Path(__file__).resolve().parent)
_STDLIB_PREFIX = str(Path(contextmanager.__code__.co_filename).resolve().parent)

_active_context: ContextVar[Context | None] = ContextVar(
    "_active_context", default=None
)

_default_context: Context | None = None


class Context:
    def __init__(
        self,
        transforms: list[GraphTransform] | None = None,
        project_root: Path | None = None,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        self._token: Token[Context | None] | None = None
        self._transforms = transforms if transforms is not None else DEFAULT_TRANSFORMS
        self.project_root: Path = (
            project_root if project_root is not None else Path.cwd()
        )
        self._group_stack: list[GroupTask] = []

    def register(self, task: Task) -> Task:
        # Capture declaration site: walk the stack past cook internals.
        frame = sys._getframe(1)
        while frame is not None:
            filename = frame.f_code.co_filename
            if not (
                filename.startswith(_COOK_PACKAGE)
                or filename.startswith(_STDLIB_PREFIX)
            ):
                task.source_file = filename
                task.source_line = frame.f_lineno
                break
            frame = frame.f_back
        if task.name in self._tasks:
            existing = self._tasks[task.name]
            existing_loc = existing.source_location
            new_loc = task.source_location
            parts = [f"Task {task.name!r}: duplicate name"]
            if existing_loc:
                parts.append(f"originally at {existing_loc}")
            if new_loc:
                parts.append(f"redefined at {new_loc}")
            raise ValueError(", ".join(parts))
        self._tasks[task.name] = task
        if self._group_stack and task is not self._group_stack[-1]:
            self._group_stack[-1]._deps.add(task)
        return task

    def sh(
        self,
        name: str,
        cmd: str | Sequence[Any],
        *,
        inputs: Sequence[str | Path | Task] | None = None,
        outputs: Sequence[str | Path] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        **extra: Any,
    ) -> ShellTask:
        task = ShellTask(
            name=name,
            cmd=cmd,
            inputs=list(inputs) if inputs is not None else [],
            outputs=list(outputs) if outputs is not None else [],
            env=env,
            cwd=cwd,
            extra=extra,
        )
        self.register(task)
        return task

    @contextmanager
    def group(self, name: str) -> Generator[GroupTask, None, None]:
        marker = self.project_root / ".cook" / "groups" / name
        task = GroupTask(name=name, outputs=[marker])
        self.register(task)
        self._group_stack.append(task)
        try:
            yield task
        finally:
            self._group_stack.pop()

    def resolve(self, path: str | Path) -> Path:
        """Resolve a path relative to the project root."""
        p = Path(path)
        if p.is_absolute():
            return p.resolve()
        return (self.project_root / p).resolve()

    def resolve_to_resource(self, path: str | Path) -> Resource:
        """Resolve a path or URL to a Resource."""
        return resolve_resource(path, self.project_root)

    def relative(self, path: str | Path) -> str:
        """Return a display string: relative for local files, label for remote."""
        resource = resolve_resource(path, self.project_root)
        if not isinstance(resource, FileResource):
            return resource.label
        try:
            return str(resource.path.relative_to(self.project_root))
        except ValueError:
            return str(resource.path)

    @property
    def db_path(self) -> Path:
        return self.project_root / ".cook" / "store.db"

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
            tasks = transform(tasks, self.project_root)
        self._tasks = tasks


def get_context() -> Context:
    ctx = _active_context.get()
    if ctx is not None:
        return ctx
    global _default_context
    if _default_context is None:
        _default_context = Context()
    return _default_context
