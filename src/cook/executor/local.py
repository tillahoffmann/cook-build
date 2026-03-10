from __future__ import annotations

import asyncio

from cook.executor import Executor, TaskExecutionError
from cook.task import ShellTask, Task


class LocalExecutor(Executor):
    def __init__(self, max_concurrent: int = 4) -> None:
        super().__init__(max_concurrent)
        self.register_handler(ShellTask, _handle_shell_task)


async def _handle_shell_task(executor: Executor, task: Task) -> None:
    assert isinstance(task, ShellTask)
    proc = await asyncio.create_subprocess_shell(
        task.cmd,
        cwd=task.cwd,
        env=task.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise TaskExecutionError(
            task=task,
            returncode=proc.returncode or 1,
            stderr=stderr.decode(),
        )
