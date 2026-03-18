"""Integration tests for SlurmExecutor against a real Slurm container.

Requires Docker. Tests are skipped when Docker is unavailable or the container
cannot be started. The container (nathanhess/slurm:full) runs a single-node
Slurm cluster.

All Slurm commands (sbatch, scontrol) execute inside the container via
``docker exec``. We patch ``slurm._run_cmd`` so the executor's subprocess
calls are routed through Docker transparently — Slurm itself is never mocked.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from cook.executor import SlurmExecutor, TaskExecutionError
from cook.executor.slurm import (
    _SBATCH_EXTRA_KEYS,
    PollTimeoutError,
    _build_wrapped_cmd,
    _parse_scontrol,
    _read_job_output,
    _run_cmd,
    _submit_job,
)
from cook.task import ShellTask

CONTAINER_NAME = f"cook-slurm-test-{uuid4()}"
IMAGE = "nathanhess/slurm:full"


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.skipif(not _docker_available(), reason="Docker not available"),
    pytest.mark.skipif(not _image_available(), reason=f"{IMAGE} not pulled"),
]


@pytest.fixture(scope="module")
def shared_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Host directory mounted into the container at /shared."""
    return tmp_path_factory.mktemp("cook-slurm")


@pytest.fixture(scope="module")
def slurm_container(shared_dir: Path) -> Generator[str]:
    """Start a Slurm container for the test module, tear it down after."""
    # Clean up any leftover container
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )

    # Start container with shared volume
    result = subprocess.run(
        [
            "docker",
            "run",
            "--platform",
            "linux/amd64",
            "--rm",
            "-d",
            "--user",
            "root",
            "--name",
            CONTAINER_NAME,
            "-v",
            f"{shared_dir}:{shared_dir}",
            IMAGE,
            "bash",
            "-c",
            "/etc/startup.sh && sleep infinity",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"Failed to start Slurm container: {result.stderr}")

    # Wait for Slurm to become ready
    for _ in range(30):
        time.sleep(1)
        r = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "sinfo", "--noheader"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0 and "idle" in r.stdout:
            break
    else:
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
        pytest.skip("Slurm did not become ready in time")

    yield CONTAINER_NAME

    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


async def _docker_run_cmd(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """Route a command through ``docker exec`` into the Slurm container."""
    docker_cmd = ["docker", "exec"]

    if env is not None:
        for k, v in env.items():
            docker_cmd.extend(["-e", f"{k}={v}"])
    if cwd is not None:
        docker_cmd.extend(["-w", cwd])

    docker_cmd.append(CONTAINER_NAME)
    docker_cmd.extend(args)

    return await _run_cmd(*docker_cmd)


@pytest.fixture()
def _patch_run_cmd(slurm_container: str) -> Generator[None]:
    """Patch _run_cmd in the slurm module to route through Docker."""
    with patch("cook.executor.slurm._run_cmd", side_effect=_docker_run_cmd):
        yield  # type: ignore[misc]


@pytest.fixture()
def executor() -> SlurmExecutor:
    return SlurmExecutor(max_concurrent=4, poll_interval=0.5)


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_runs_simple_command(executor: SlurmExecutor) -> None:
    task = ShellTask(name="slurm-echo", cmd="echo hello")
    await executor.execute(task)


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_writes_output_file(
    executor: SlurmExecutor,
    slurm_container: str,
) -> None:
    outfile = "/tmp/cook-test-output.txt"
    task = ShellTask(name="slurm-write", cmd=f"echo hello > {outfile}")
    await executor.execute(task)

    # Verify the file was created inside the container
    result = subprocess.run(
        ["docker", "exec", slurm_container, "cat", outfile],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_raises_on_nonzero_exit(executor: SlurmExecutor) -> None:
    task = ShellTask(name="slurm-fail", cmd="exit 42")
    with pytest.raises(TaskExecutionError) as exc_info:
        await executor.execute(task)
    assert exc_info.value.returncode == 42


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_error_contains_job_id(executor: SlurmExecutor) -> None:
    task = ShellTask(name="slurm-fail-detail", cmd="exit 1")
    with pytest.raises(TaskExecutionError) as exc_info:
        await executor.execute(task)
    # stderr should mention the Slurm job ID
    assert "Slurm job" in exc_info.value.stderr


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_error_stderr_content(
    executor: SlurmExecutor,
    shared_dir: Path,
) -> None:
    """Verify stderr file content is actually read end-to-end via shared mount."""
    task = ShellTask(
        name="slurm-stderr-content",
        cmd="echo 'this is the error' >&2; exit 1",
        cwd=str(shared_dir),
    )
    with pytest.raises(TaskExecutionError) as exc_info:
        await executor.execute(task)
    assert "this is the error" in exc_info.value.stderr


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_cwd(
    executor: SlurmExecutor,
    slurm_container: str,
) -> None:
    outfile = "/tmp/cook-test-cwd.txt"
    task = ShellTask(name="slurm-cwd", cmd=f"pwd > {outfile}", cwd="/tmp")
    await executor.execute(task)

    result = subprocess.run(
        ["docker", "exec", slurm_container, "cat", outfile],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "/tmp"


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_concurrent_jobs(executor: SlurmExecutor) -> None:
    """Multiple jobs can be submitted and polled concurrently."""
    tasks = [ShellTask(name=f"slurm-concurrent-{i}", cmd="sleep 1") for i in range(3)]
    await asyncio.gather(*(executor.execute(t) for t in tasks))


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_submit_failure(executor: SlurmExecutor) -> None:
    """sbatch fails when given an invalid partition."""
    task = ShellTask(name="slurm-bad-submit", cmd="echo hi")

    async def bad_sbatch(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        if args[0] == "sbatch" or (len(args) > 2 and args[2] == "sbatch"):
            return 1, "", "sbatch: error: invalid partition"
        return await _docker_run_cmd(*args, env=env, cwd=cwd)

    with patch("cook.executor.slurm._run_cmd", side_effect=bad_sbatch):
        with pytest.raises(TaskExecutionError) as exc_info:
            await executor.execute(task)
        assert exc_info.value.returncode == 1


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_submit_bad_output(executor: SlurmExecutor) -> None:
    """sbatch returns non-numeric output."""
    task = ShellTask(name="slurm-bad-output", cmd="echo hi")

    async def bad_output(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        if args[0] == "sbatch" or (len(args) > 2 and args[2] == "sbatch"):
            return 0, "garbage output", ""
        return await _docker_run_cmd(*args, env=env, cwd=cwd)

    with patch("cook.executor.slurm._run_cmd", side_effect=bad_output):
        with pytest.raises(TaskExecutionError, match="unexpected output"):
            await executor.execute(task)


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_read_job_output_scontrol_failure() -> None:
    """_read_job_output returns empty string when scontrol fails."""

    async def failing_scontrol(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 1, "", "error"

    with patch("cook.executor.slurm._run_cmd", side_effect=failing_scontrol):
        result = await _read_job_output("99999")
    assert result == ""


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_read_job_output_devnull() -> None:
    """_read_job_output returns empty when StdErr=/dev/null."""

    async def devnull_scontrol(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 0, "StdErr=/dev/null StdOut=/dev/null JobState=FAILED", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=devnull_scontrol):
        result = await _read_job_output("99999")
    assert result == ""


async def test_poll_timeout_raises() -> None:
    """_poll_job raises PollTimeoutError when scontrol always returns RUNNING."""
    call_count = 0

    async def always_running(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        nonlocal call_count
        call_count += 1
        return 0, "JobState=RUNNING ExitCode=0:0", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=always_running):
        with pytest.raises(PollTimeoutError, match="timed out"):
            from cook.executor.slurm import _poll_job

            await _poll_job("123", poll_interval=0.01, poll_timeout=0.05)
    assert call_count >= 2


async def test_poll_timeout_on_scontrol_failure() -> None:
    """_poll_job raises PollTimeoutError when scontrol consistently fails."""

    async def failing(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 1, "", "error"

    with patch("cook.executor.slurm._run_cmd", side_effect=failing):
        with pytest.raises(PollTimeoutError, match="timed out"):
            from cook.executor.slurm import _poll_job

            await _poll_job(
                "123",
                poll_interval=0.01,
                poll_timeout=0.05,
                poll_retries=9999,
            )


async def test_poll_timeout_through_handler_calls_scancel() -> None:
    """PollTimeoutError through execute() triggers scancel and re-raises."""
    submitted_job_id = ""
    cancelled_job_id = ""

    async def fake_run_cmd(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        nonlocal submitted_job_id, cancelled_job_id
        if args[0] == "sbatch":
            submitted_job_id = "42"
            return 0, "42", ""
        if args[0] == "scontrol":
            return 0, "JobState=RUNNING ExitCode=0:0", ""
        if args[0] == "scancel":
            cancelled_job_id = args[1]
            return 0, "", ""
        return 1, "", "unknown"

    executor = SlurmExecutor(max_concurrent=1, poll_interval=0.01, poll_timeout=0.05)
    task = ShellTask(name="timeout-handler", cmd="sleep 999")

    with patch("cook.executor.slurm._run_cmd", side_effect=fake_run_cmd):
        with pytest.raises(PollTimeoutError):
            await executor.execute(task)

    assert cancelled_job_id == submitted_job_id == "42"


async def test_read_job_output_no_file_handle_leak(tmp_path: object) -> None:
    """_read_job_output properly closes files (uses Path.read_text)."""
    from pathlib import Path

    stderr_file = Path(str(tmp_path)) / "slurm-99.out"
    stderr_file.write_text("some error output")

    async def scontrol_with_file(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 0, f"StdErr={stderr_file} StdOut=/dev/null JobState=FAILED", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=scontrol_with_file):
        result = await _read_job_output("99")
    assert result == "some error output"


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_env_passed_to_job_not_sbatch(
    executor: SlurmExecutor,
    slurm_container: str,
) -> None:
    """task.env should be exported into the job, not applied to sbatch itself."""
    outfile = "/tmp/cook-test-env.txt"
    task = ShellTask(
        name="slurm-env",
        cmd=f"echo MY_VAR=$MY_VAR > {outfile}",
        env={"MY_VAR": "hello"},
    )
    await executor.execute(task)

    result = subprocess.run(
        ["docker", "exec", slurm_container, "cat", outfile],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "MY_VAR=hello"


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_env_with_comma(
    executor: SlurmExecutor,
    slurm_container: str,
) -> None:
    """Env values containing commas must be passed through correctly."""
    outfile = "/tmp/cook-test-env-comma.txt"
    task = ShellTask(
        name="slurm-env-comma",
        cmd=f"echo MY_LIST=$MY_LIST > {outfile}",
        env={"MY_LIST": "a,b,c"},
    )
    await executor.execute(task)

    result = subprocess.run(
        ["docker", "exec", slurm_container, "cat", outfile],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "MY_LIST=a,b,c"


@pytest.mark.usefixtures("_patch_run_cmd")
async def test_slurm_cancel_on_error(
    slurm_container: str,
) -> None:
    """Cancelled execute() calls scancel on the job."""
    executor = SlurmExecutor(max_concurrent=4, poll_interval=0.2, poll_timeout=60)
    task = ShellTask(name="slurm-cancel", cmd="sleep 300")

    t = asyncio.ensure_future(executor.execute(task))
    await asyncio.sleep(1)
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t

    # Give scancel a moment to run
    await asyncio.sleep(1)

    # Find the job ID (most recent) and verify it was cancelled
    r = subprocess.run(
        ["docker", "exec", slurm_container, "squeue", "--noheader", "-o", "%i %T"],
        capture_output=True,
        text=True,
    )
    # squeue should not show the job as RUNNING anymore
    for line in r.stdout.strip().splitlines():
        if "RUNNING" in line:
            pytest.fail(f"Job still running after cancel: {line}")


def test_build_wrapped_cmd_rejects_invalid_env_key() -> None:
    """Env keys must be valid shell identifiers."""
    task = ShellTask(name="bad-key", cmd="echo hi", env={"GOOD": "ok"})
    # Valid key should work
    _build_wrapped_cmd(task)

    task_bad = ShellTask(name="bad-key", cmd="echo hi", env={"BAD KEY": "val"})
    with pytest.raises(ValueError, match="Invalid environment variable name"):
        _build_wrapped_cmd(task_bad)


def test_build_wrapped_cmd_rejects_injection_env_key() -> None:
    """Env keys with shell metacharacters must be rejected."""
    task = ShellTask(name="inject", cmd="echo hi", env={"FOO;rm -rf /;X": "val"})
    with pytest.raises(ValueError, match="Invalid environment variable name"):
        _build_wrapped_cmd(task)


def test_build_wrapped_cmd_rejects_digit_start_env_key() -> None:
    """Env keys starting with a digit are not valid shell identifiers."""
    task = ShellTask(name="digit", cmd="echo hi", env={"1FOO": "val"})
    with pytest.raises(ValueError, match="Invalid environment variable name"):
        _build_wrapped_cmd(task)


async def test_poll_raises_on_consecutive_scontrol_errors() -> None:
    """_poll_job raises PollTimeoutError with last error after max consecutive failures."""

    async def failing(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 1, "", "scontrol: error: Invalid job id"

    with patch("cook.executor.slurm._run_cmd", side_effect=failing):
        with pytest.raises(PollTimeoutError, match="Invalid job id"):
            from cook.executor.slurm import _poll_job

            await _poll_job(
                "123",
                poll_interval=0.01,
                poll_timeout=10.0,
                poll_retries=3,
            )


async def test_poll_recovers_from_transient_scontrol_errors() -> None:
    """Transient scontrol failures should not count toward max errors."""
    call_count = 0

    async def transient_then_ok(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return 1, "", "transient error"
        return 0, "JobState=COMPLETED ExitCode=0:0", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=transient_then_ok):
        from cook.executor.slurm import _poll_job

        state, exit_code = await _poll_job(
            "123",
            poll_interval=0.01,
            poll_timeout=10.0,
            poll_retries=5,
        )
    assert state == "COMPLETED"
    assert exit_code == 0


async def test_poll_exit_code_signal_killed() -> None:
    """Signal-killed jobs should report 128 + signal as exit code."""

    async def signal_killed(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 0, "JobState=FAILED ExitCode=0:9", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=signal_killed):
        from cook.executor.slurm import _poll_job

        state, exit_code = await _poll_job("123", poll_interval=0.01, poll_timeout=10.0)
    assert state == "FAILED"
    assert exit_code == 137  # 128 + 9 (SIGKILL)


async def test_poll_exit_code_normal_failure() -> None:
    """Normal exit code should be preserved (not affected by signal logic)."""

    async def normal_failure(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 0, "JobState=FAILED ExitCode=42:0", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=normal_failure):
        from cook.executor.slurm import _poll_job

        state, exit_code = await _poll_job("123", poll_interval=0.01, poll_timeout=10.0)
    assert state == "FAILED"
    assert exit_code == 42


async def test_poll_exit_code_malformed_treated_as_nonzero() -> None:
    """Malformed ExitCode (e.g. N/A on NODE_FAIL) should not crash."""

    async def node_fail(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        return 0, "JobState=NODE_FAIL ExitCode=N/A", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=node_fail):
        from cook.executor.slurm import _poll_job

        state, exit_code = await _poll_job("123", poll_interval=0.01, poll_timeout=10.0)
    assert state == "NODE_FAIL"
    assert exit_code != 0


async def test_cancel_job_timeout() -> None:
    """_cancel_job should not hang indefinitely if scancel hangs."""
    from cook.executor.slurm import _cancel_job

    async def hanging_scancel(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        await asyncio.sleep(999)
        return 0, "", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=hanging_scancel):
        # Should complete quickly despite scancel hanging, not raise
        await _cancel_job("123", timeout=0.1)


def test_parse_scontrol_values_with_spaces() -> None:
    """Values containing spaces should not be truncated."""
    output = "StdErr=/path with spaces/slurm.out StdOut=/dev/null JobState=FAILED"
    info = _parse_scontrol(output)
    assert info["StdErr"] == "/path with spaces/slurm.out"
    assert info["StdOut"] == "/dev/null"
    assert info["JobState"] == "FAILED"


def test_parse_scontrol_real_output() -> None:
    """Parse realistic scontrol output."""
    output = (
        "JobId=42 JobName=wrap\n"
        "   JobState=COMPLETED Reason=None\n"
        "   ExitCode=0:0\n"
        "   StdErr=/home/user/slurm-42.out\n"
        "   StdOut=/home/user/slurm-42.out"
    )
    info = _parse_scontrol(output)
    assert info["JobId"] == "42"
    assert info["JobState"] == "COMPLETED"
    assert info["ExitCode"] == "0:0"
    assert info["StdErr"] == "/home/user/slurm-42.out"


def test_parse_scontrol_multiline() -> None:
    """Multiline scontrol output should be parsed correctly."""
    output = (
        "JobId=42 JobName=test\n"
        "   JobState=COMPLETED ExitCode=0:0\n"
        "   StdErr=/tmp/slurm-42.out"
    )
    info = _parse_scontrol(output)
    assert info["JobId"] == "42"
    assert info["JobState"] == "COMPLETED"
    assert info["StdErr"] == "/tmp/slurm-42.out"


async def test_submit_job_extra_sbatch_flags() -> None:
    """Extra keys on the task are passed as sbatch flags."""
    task = ShellTask(
        name="gpu-job",
        cmd="train.py",
        extra={"slurm": {"mem": "8G", "time": "01:00:00", "partition": "gpu"}},
    )
    captured_cmd: list[str] = []

    async def fake_run_cmd(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        captured_cmd.extend(args)
        return 0, "12345\n", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=fake_run_cmd):
        job_id = await _submit_job(task)

    assert job_id == "12345"
    assert "--mem" in captured_cmd
    assert captured_cmd[captured_cmd.index("--mem") + 1] == "8G"
    assert "--time" in captured_cmd
    assert captured_cmd[captured_cmd.index("--time") + 1] == "01:00:00"
    assert "--partition" in captured_cmd
    assert captured_cmd[captured_cmd.index("--partition") + 1] == "gpu"


async def test_submit_job_no_extra() -> None:
    """Without extra, no resource flags are added."""
    task = ShellTask(name="plain", cmd="echo hi")
    captured_cmd: list[str] = []

    async def fake_run_cmd(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        captured_cmd.extend(args)
        return 0, "99\n", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=fake_run_cmd):
        await _submit_job(task)

    # Only sbatch, --parsable, --wrap, and the command itself
    for flag in _SBATCH_EXTRA_KEYS.values():
        assert flag not in captured_cmd


async def test_submit_job_defaults_merged() -> None:
    """Config defaults are merged, with task extra taking precedence."""
    task = ShellTask(
        name="job",
        cmd="run.py",
        extra={"slurm": {"mem": "16G"}},  # overrides default
    )
    defaults = {"mem": "4G", "partition": "batch"}
    captured_cmd: list[str] = []

    async def fake_run_cmd(
        *args: str, env: dict[str, str] | None = None, cwd: str | None = None
    ) -> tuple[int, str, str]:
        captured_cmd.extend(args)
        return 0, "100\n", ""

    with patch("cook.executor.slurm._run_cmd", side_effect=fake_run_cmd):
        await _submit_job(task, defaults)

    # Task extra overrides default
    assert captured_cmd[captured_cmd.index("--mem") + 1] == "16G"
    # Default applied
    assert "--partition" in captured_cmd
    assert captured_cmd[captured_cmd.index("--partition") + 1] == "batch"


# --- validate_tasks ---


def test_validate_tasks_valid_keys() -> None:
    tasks = {
        "t": ShellTask(
            name="t",
            cmd="run.py",
            extra={"slurm": {"mem": "8G", "partition": "gpu"}},
        )
    }
    SlurmExecutor.validate_tasks(tasks)  # should not raise


def test_validate_tasks_no_slurm_key() -> None:
    tasks = {"t": ShellTask(name="t", cmd="run.py")}
    SlurmExecutor.validate_tasks(tasks)  # should not raise


def test_validate_tasks_unknown_key() -> None:
    tasks = {
        "t": ShellTask(
            name="t",
            cmd="run.py",
            extra={"slurm": {"mem": "8G", "memory": "16G"}},
        )
    }
    with pytest.raises(ValueError, match="unknown slurm option.*memory"):
        SlurmExecutor.validate_tasks(tasks)


def test_validate_tasks_slurm_not_dict() -> None:
    tasks = {"t": ShellTask(name="t", cmd="run.py", extra={"slurm": "bad"})}
    with pytest.raises(ValueError, match="must be a dict"):
        SlurmExecutor.validate_tasks(tasks)


async def test_group_task_handler(tmp_path: Path) -> None:
    from cook.executor.slurm import SlurmExecutor
    from cook.task import GroupTask

    marker = tmp_path / ".cook" / "groups" / "my-group"
    task = GroupTask(name="my-group", outputs=[str(marker)])
    executor = SlurmExecutor(max_concurrent=1)
    await executor.execute(task)
    assert marker.exists()
