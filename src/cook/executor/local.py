from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..config import ConfigError
from ..task import ShellTask, Task
from . import Executor, TaskExecutionError, register_executor


@dataclass
class LocalConfig:
    max_concurrent: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.max_concurrent, int) or isinstance(
            self.max_concurrent, bool
        ):
            raise ConfigError(
                f"Expected 'local.max_concurrent' to be an integer, "
                f"got {type(self.max_concurrent).__name__}"
            )
        if self.max_concurrent < 1:
            raise ConfigError(
                f"'local.max_concurrent' must be >= 1, got {self.max_concurrent}"
            )


@register_executor("local")
class LocalExecutor(Executor):
    def __init__(self, max_concurrent: int = 1, stream: bool = False) -> None:
        super().__init__(max_concurrent, stream=stream)

    @classmethod
    def from_config(
        cls, executor_config: dict[str, Any], jobs: int | None = None
    ) -> LocalExecutor:
        cfg = LocalConfig(**executor_config)
        return cls(max_concurrent=jobs if jobs is not None else cfg.max_concurrent)


@LocalExecutor.register_handler(task_type=ShellTask)
async def _handle_shell_task(executor: Executor, task: Task) -> None:
    assert isinstance(task, ShellTask)
    if executor.stream:
        # Pass-through mode: no capture, output goes directly to terminal
        proc = await asyncio.create_subprocess_shell(
            task.cmd,
            cwd=task.cwd,
            env=task.env,
        )
        await proc.communicate()
        if proc.returncode:
            raise TaskExecutionError(
                task=task,
                returncode=proc.returncode,
                stderr="(output was streamed to terminal)",
            )
    else:
        # Capture mode: capture both stdout and stderr
        proc = await asyncio.create_subprocess_shell(
            task.cmd,
            cwd=task.cwd,
            env=task.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await proc.communicate()
        if proc.returncode:
            raise TaskExecutionError(
                task=task,
                returncode=proc.returncode,
                stderr=stderr_bytes.decode(errors="replace") if stderr_bytes else "",
                stdout=stdout_bytes.decode(errors="replace") if stdout_bytes else "",
            )
