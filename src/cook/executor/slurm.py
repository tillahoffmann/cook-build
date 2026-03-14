from __future__ import annotations

import asyncio
import re
import shlex
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ConfigError
from ..task import ShellTask, Task
from . import Executor, TaskExecutionError, register_executor

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


@dataclass
class SlurmConfig:
    max_concurrent: int = 64
    poll_interval: float = 2.0
    poll_timeout: float = 86400.0
    poll_retries: int = 10
    defaults: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.max_concurrent, int) or isinstance(
            self.max_concurrent, bool
        ):
            raise ConfigError(
                f"Expected 'slurm.max_concurrent' to be an integer, "
                f"got {type(self.max_concurrent).__name__}"
            )
        if self.max_concurrent < 1:
            raise ConfigError(
                f"'slurm.max_concurrent' must be >= 1, got {self.max_concurrent}"
            )
        if not isinstance(self.poll_interval, (int, float)) or isinstance(
            self.poll_interval, bool
        ):
            raise ConfigError(
                f"Expected 'slurm.poll_interval' to be a number, "
                f"got {type(self.poll_interval).__name__}"
            )
        if self.poll_interval <= 0:
            raise ConfigError(
                f"'slurm.poll_interval' must be > 0, got {self.poll_interval}"
            )
        self.poll_interval = float(self.poll_interval)
        if not isinstance(self.poll_timeout, (int, float)) or isinstance(
            self.poll_timeout, bool
        ):
            raise ConfigError(
                f"Expected 'slurm.poll_timeout' to be a number, "
                f"got {type(self.poll_timeout).__name__}"
            )
        if self.poll_timeout <= 0:
            raise ConfigError(
                f"'slurm.poll_timeout' must be > 0, got {self.poll_timeout}"
            )
        self.poll_timeout = float(self.poll_timeout)
        if not isinstance(self.poll_retries, int) or isinstance(
            self.poll_retries, bool
        ):
            raise ConfigError(
                f"Expected 'slurm.poll_retries' to be an integer, "
                f"got {type(self.poll_retries).__name__}"
            )
        if self.poll_retries < 1:
            raise ConfigError(
                f"'slurm.poll_retries' must be >= 1, got {self.poll_retries}"
            )
        if not isinstance(self.defaults, dict):
            raise ConfigError(
                f"Expected 'slurm.defaults' to be a table, "
                f"got {type(self.defaults).__name__}"
            )
        for k, v in self.defaults.items():
            if not isinstance(v, str):
                raise ConfigError(
                    f"Expected 'slurm.defaults.{k}' to be a string, "
                    f"got {type(v).__name__}"
                )


@register_executor("slurm", config_cls=SlurmConfig)
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
    def from_config(
        cls, executor_config: dict[str, Any], jobs: int | None = None
    ) -> SlurmExecutor:
        cfg = SlurmConfig(**executor_config)
        return cls(
            max_concurrent=jobs if jobs is not None else cfg.max_concurrent,
            poll_interval=cfg.poll_interval,
            poll_timeout=cfg.poll_timeout,
            poll_retries=cfg.poll_retries,
            defaults=cfg.defaults,
        )

    @classmethod
    def validate_tasks(cls, tasks: Mapping[str, Task]) -> None:
        valid_keys = set(_SBATCH_EXTRA_KEYS)
        for task in tasks.values():
            slurm_opts = task.extra.get("slurm")
            if slurm_opts is None:
                continue
            if not isinstance(slurm_opts, dict):
                raise ValueError(
                    f"Task {task.name!r}: 'slurm' must be a dict, "
                    f"got {type(slurm_opts).__name__}"
                )
            unknown = set(slurm_opts) - valid_keys
            if unknown:
                raise ValueError(
                    f"Task {task.name!r}: unknown slurm option(s): "
                    f"{', '.join(sorted(unknown))}. "
                    f"Valid options: {', '.join(sorted(valid_keys))}"
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
    # Merge defaults with task-level slurm opts; task opts take precedence
    merged = {**(defaults or {}), **task.extra.get("slurm", {})}
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
