from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ..task import ShellTask, Task
from . import Executor, TaskExecutionError, register_executor

if TYPE_CHECKING:
    from ..config import Config


@register_executor("local")
class LocalExecutor(Executor):
    def __init__(self, max_concurrent: int = 1) -> None:
        super().__init__(max_concurrent)

    @classmethod
    def from_config(cls, config: Config, jobs: int | None = None) -> LocalExecutor:
        return cls(
            max_concurrent=jobs if jobs is not None else config.local_max_concurrent
        )


@LocalExecutor.register_handler(task_type=ShellTask)
async def _handle_shell_task(executor: Executor, task: Task) -> None:
    assert isinstance(task, ShellTask)
    proc = await asyncio.create_subprocess_shell(
        task.cmd,
        cwd=task.cwd,
        env=task.env,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode:
        raise TaskExecutionError(
            task=task,
            returncode=proc.returncode,
            stderr=stderr.decode(errors="replace") if stderr else "",
        )
