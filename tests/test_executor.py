from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from cook.executor import Executor, LocalExecutor, TaskExecutionError
from cook.task import ShellTask, Task


async def test_local_executor_runs_echo() -> None:
    executor = LocalExecutor()
    task = ShellTask(name="echo-test", cmd="echo hello")
    await executor.execute(task)


async def test_local_executor_captures_stdout(tmp_path: object) -> None:
    outfile = str(tmp_path) + "/out.txt"  # type: ignore[operator]
    executor = LocalExecutor()
    task = ShellTask(name="capture", cmd=f"echo hello > {outfile}")
    await executor.execute(task)
    assert open(outfile).read().strip() == "hello"


async def test_local_executor_raises_on_nonzero_exit() -> None:
    executor = LocalExecutor()
    task = ShellTask(name="fail", cmd="exit 1")
    with pytest.raises(TaskExecutionError):
        await executor.execute(task)


async def test_task_execution_error_contains_details() -> None:
    executor = LocalExecutor()
    task = ShellTask(name="fail-detail", cmd="echo badstuff >&2; exit 42")
    with pytest.raises(TaskExecutionError) as exc_info:
        await executor.execute(task)
    err = exc_info.value
    assert err.task is task
    assert err.returncode == 42
    assert "badstuff" in err.stderr
    assert "fail-detail" in str(err)


async def test_semaphore_limits_concurrency() -> None:
    max_concurrent = 2
    executor = LocalExecutor(max_concurrent=max_concurrent)
    running = 0
    peak = 0

    original_handler = LocalExecutor._handlers[ShellTask]

    async def tracking_handler(ex: Executor, task: Task) -> None:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1

    LocalExecutor.register_handler(tracking_handler, task_type=ShellTask)
    try:
        tasks = [ShellTask(name=f"t{i}", cmd="true") for i in range(max_concurrent + 1)]
        await asyncio.gather(*(executor.execute(t) for t in tasks))
        assert peak <= max_concurrent
    finally:
        LocalExecutor.register_handler(original_handler, task_type=ShellTask)


async def test_mro_handler_resolution() -> None:
    @dataclass
    class MyShellTask(ShellTask):
        pass

    executor = LocalExecutor()
    task = MyShellTask(name="sub", cmd="echo mro")
    # Should use ShellTask handler via MRO
    await executor.execute(task)


async def test_unregistered_task_type_raises() -> None:
    @dataclass
    class CustomTask(Task):
        pass

    executor = LocalExecutor()
    task = CustomTask(name="custom")
    with pytest.raises(TypeError, match="No handler registered"):
        await executor.execute(task)


async def test_custom_handler() -> None:
    @dataclass
    class CustomTask(Task):
        value: str = ""

    called_with: list[str] = []

    async def handle_custom(executor: Executor, task: Task) -> None:
        assert isinstance(task, CustomTask)
        called_with.append(task.value)

    executor = LocalExecutor()
    LocalExecutor.register_handler(handle_custom, task_type=CustomTask)
    try:
        task = CustomTask(name="custom", value="hello")
        await executor.execute(task)
        assert called_with == ["hello"]
    finally:
        del LocalExecutor._handlers[CustomTask]


async def test_env_none_inherits_parent() -> None:
    executor = LocalExecutor()
    task = ShellTask(name="env-inherit", cmd="echo $HOME", env=None)
    # Should not raise -- inherits env and can run
    await executor.execute(task)


async def test_env_dict_replaces_environment(tmp_path: object) -> None:
    outfile = str(tmp_path) + "/env_out.txt"  # type: ignore[operator]
    executor = LocalExecutor()
    # Use a minimal env with PATH so shell can find echo
    task = ShellTask(
        name="env-replace",
        cmd=f"echo HOME=$HOME > {outfile}",
        env={"FOO": "bar", "PATH": "/usr/bin:/bin"},
    )
    await executor.execute(task)
    content = open(outfile).read().strip()
    assert content == "HOME="


async def test_cwd_sets_working_directory(tmp_path: object) -> None:
    outfile = str(tmp_path) + "/cwd_out.txt"  # type: ignore[operator]
    executor = LocalExecutor()
    task = ShellTask(name="cwd-test", cmd=f"pwd > {outfile}", cwd=str(tmp_path))
    await executor.execute(task)
    result = open(outfile).read().strip()
    # Resolve symlinks for macOS /private/var/... vs /var/...
    from pathlib import Path

    assert Path(result).resolve() == Path(str(tmp_path)).resolve()


async def test_stderr_invalid_utf8() -> None:
    """Binary stderr should not crash error reporting."""
    executor = LocalExecutor()
    # printf \xff produces an invalid UTF-8 byte
    task = ShellTask(name="bad-utf8", cmd="printf '\\xff' >&2; exit 1")
    with pytest.raises(TaskExecutionError) as exc_info:
        await executor.execute(task)
    assert exc_info.value.returncode == 1
    # Should contain the replacement character, not crash
    assert isinstance(exc_info.value.stderr, str)
