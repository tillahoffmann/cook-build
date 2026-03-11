from __future__ import annotations

import asyncio

from ..task import ShellTask, Task
from . import Executor, TaskExecutionError, register_executor


@register_executor("local")
class LocalExecutor(Executor):
    def __init__(self, max_concurrent: int = 1) -> None:
        super().__init__(max_concurrent)


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
    if proc.returncode != 0:
        raise TaskExecutionError(
            task=task,
            returncode=proc.returncode or 1,
            stderr=stderr.decode() if stderr else "",
        )
