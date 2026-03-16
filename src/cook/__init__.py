from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .context import Context, get_context
from .task import ShellTask, Task


def sh(
    name: str,
    cmd: str,
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


__all__ = ["Context", "ShellTask", "Task", "get_context", "sh"]
