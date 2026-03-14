from __future__ import annotations

import asyncio
import re
import shlex
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..task import ShellTask, Task
from . import Executor, TaskExecutionError, register_executor

if TYPE_CHECKING:
    from ..config import Config

# scontrol job states that indicate the job is still active
_ACTIVE_STATES = frozenset(
    {
        "PENDING",
        "RUNNING",
        "REQUEUED",
        "SUSPENDED",
        "CONFIGURING",
        "COMPLETING",
    }
)

_SUCCESS_STATES = frozenset({"COMPLETED"})

_KV_RE = re.compile(r"(?:^|(?<=\s))(\w+)=(.*?)(?=\s\w+=|$)", re.MULTILINE)
_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")


class PollTimeoutError(Exception):
    """Raised when polling for job completion exceeds the timeout."""


@register_executor("slurm")
class SlurmExecutor(Executor):
    def __init__(
        self,
        max_concurrent: int = 64,
        poll_interval: float = 2.0,
        poll_timeout: float = 86400.0,
        poll_retries: int = 10,
        defaults: dict[str, str] | None = None,
    ) -> None:
        super().__init__(max_concurrent)
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.poll_retries = poll_retries
        self.defaults: dict[str, str] = defaults or {}

    @classmethod
    def from_config(cls, config: Config, jobs: int | None = None) -> SlurmExecutor:
        return cls(
            max_concurrent=jobs if jobs is not None else config.slurm_max_concurrent,
            poll_interval=config.slurm_poll_interval,
            poll_timeout=config.slurm_poll_timeout,
            poll_retries=config.slurm_poll_retries,
            defaults=config.slurm_defaults,
        )


async def _run_cmd(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace").strip(),
        stderr_bytes.decode(errors="replace").strip(),
    )


def _parse_scontrol(output: str) -> dict[str, str]:
    """Parse key=value pairs from scontrol show job output."""
    return dict(_KV_RE.findall(output))


def _build_wrapped_cmd(task: ShellTask) -> str:
    """Build the shell command for --wrap, embedding env exports if needed."""
    if not task.env:
        return task.cmd
    for k in task.env:
        if not _ENV_KEY_RE.match(k):
            raise ValueError(
                f"Invalid environment variable name: {k!r}. "
                "Must match [A-Za-z_][A-Za-z0-9_]*"
            )
    # Embed 'export K=V' before the user command so values with commas,
    # spaces, or other special chars are handled correctly.
    exports = " ".join(f"export {k}={shlex.quote(v)};" for k, v in task.env.items())
    return f"{exports} {task.cmd}"


_SBATCH_EXTRA_KEYS = {
    "mem": "--mem",
    "time": "--time",
    "partition": "--partition",
    "gres": "--gres",
    "constraint": "--constraint",
    "account": "--account",
    "qos": "--qos",
    "nodes": "--nodes",
    "ntasks": "--ntasks",
    "cpus_per_task": "--cpus-per-task",
}


async def _submit_job(task: ShellTask, defaults: dict[str, str] | None = None) -> str:
    """Submit a job via sbatch --wrap and return the job ID."""
    cmd: list[str] = ["sbatch", "--parsable"]
    if task.cwd:
        cmd.extend(["--chdir", task.cwd])
    # Merge defaults with task extra; task extra takes precedence
    merged = {**(defaults or {}), **task.extra}
    for key, flag in _SBATCH_EXTRA_KEYS.items():
        if key in merged:
            cmd.extend([flag, str(merged[key])])
    cmd.extend(["--wrap", _build_wrapped_cmd(task)])

    rc, stdout, stderr = await _run_cmd(*cmd)
    if rc != 0:
        raise TaskExecutionError(task=task, returncode=rc, stderr=stderr)

    job_id = stdout.strip().split(";")[0]  # strip cluster name if present
    if not job_id.isdigit():
        raise TaskExecutionError(
            task=task,
            returncode=1,
            stderr=f"sbatch returned unexpected output: {stdout}",
        )
    return job_id


async def _poll_job(
    job_id: str,
    poll_interval: float,
    poll_timeout: float = 86400.0,
    poll_retries: int = 10,
) -> tuple[str, int]:
    """Poll scontrol until the job reaches a terminal state.

    Returns (state, exit_code). Raises PollTimeoutError if the job does not
    reach a terminal state within poll_timeout seconds or after
    poll_retries consecutive scontrol failures.
    """
    deadline = time.monotonic() + poll_timeout
    consecutive_errors = 0
    last_error = ""
    while True:
        rc, stdout, stderr = await _run_cmd("scontrol", "show", "job", job_id)
        if rc == 0 and stdout:
            consecutive_errors = 0
            info = _parse_scontrol(stdout)
            state = info.get("JobState", "UNKNOWN")
            if state not in _ACTIVE_STATES:
                # ExitCode format is "returncode:signal" but may be
                # malformed (e.g. "N/A") on infrastructure failures.
                try:
                    parts = info.get("ExitCode", "0:0").split(":")
                    rc = int(parts[0])
                    signal = int(parts[1]) if len(parts) > 1 else 0
                    exit_code = rc if rc != 0 else (128 + signal if signal else 0)
                except ValueError:
                    exit_code = 1
                return state, exit_code
        else:
            consecutive_errors += 1
            last_error = stderr
            if consecutive_errors >= poll_retries:
                raise PollTimeoutError(
                    f"Slurm job {job_id}: scontrol failed {consecutive_errors} "
                    f"consecutive times: {last_error}"
                )
        if time.monotonic() >= deadline:
            raise PollTimeoutError(
                f"Slurm job {job_id} timed out after {poll_timeout}s"
            )
        await asyncio.sleep(poll_interval)


async def _cancel_job(job_id: str, timeout: float = 30.0) -> None:
    """Cancel a Slurm job via scancel."""
    # FIXME: log a warning on scancel failure once we have logging — the job
    # may keep running on the cluster.
    try:
        await asyncio.wait_for(_run_cmd("scancel", job_id), timeout=timeout)
    except TimeoutError:
        pass


async def _read_job_output(job_id: str) -> str:
    """Read the job's stderr output file via scontrol."""
    rc, stdout, _ = await _run_cmd("scontrol", "show", "job", job_id)
    if rc != 0:
        return ""

    info = _parse_scontrol(stdout)
    stderr_path = info.get("StdErr", "")

    if not stderr_path or stderr_path == "/dev/null":
        return ""

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: Path(stderr_path).read_text(errors="replace")
        )
    except OSError:
        return ""


@SlurmExecutor.register_handler(task_type=ShellTask)
async def _handle_shell_task(executor: Executor, task: Task) -> None:
    assert isinstance(task, ShellTask)
    assert isinstance(executor, SlurmExecutor)

    job_id = await _submit_job(task, executor.defaults)
    try:
        state, exit_code = await _poll_job(
            job_id,
            executor.poll_interval,
            executor.poll_timeout,
            executor.poll_retries,
        )
    except (asyncio.CancelledError, PollTimeoutError):
        await _cancel_job(job_id)
        raise

    if state not in _SUCCESS_STATES or exit_code != 0:
        stderr = await _read_job_output(job_id)
        raise TaskExecutionError(
            task=task,
            returncode=exit_code if exit_code != 0 else 1,
            stderr=f"Slurm job {job_id} {state}\n{stderr}".strip(),
        )
