from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .context import Context, get_context
from .task import GroupTask, ShellTask, Task


def sh(
    name: str,
    cmd: str | Sequence[str],
    *,
    inputs: Sequence[str | Path | Task] | None = None,
    outputs: Sequence[str | Path] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    **extra: Any,
) -> ShellTask:
    return get_context().sh(
        name, cmd, inputs=inputs, outputs=outputs, env=env, cwd=cwd, **extra
    )


@contextmanager
def group(name: str) -> Generator[GroupTask, None, None]:
    with get_context().group(name) as g:
        yield g


def create_task(*args: Any, **kwargs: Any) -> None:
    raise NotImplementedError(
        "create_task() was removed in cook-build 0.2. "
        "Use cook.sh() instead:\n\n"
        "  from cook import sh\n"
        "  sh(name='my-task', cmd='echo hello', inputs=[...], outputs=[...])\n\n"
        "For the old API, pin cook-build<1.0 in your dependencies."
    )


__all__ = [
    "Context",
    "GroupTask",
    "ShellTask",
    "Task",
    "create_task",
    "get_context",
    "group",
    "sh",
]
