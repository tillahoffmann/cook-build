"""End-to-end integration tests for GCS-backed tasks.

Requires Docker to run fake-gcs-server. Tests exercise the full build pipeline
(scheduler, executor, store, resource) with gs:// inputs and outputs.
Fixtures from conftest.py handle the container lifecycle and env setup.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from cook.executor import LocalExecutor
from cook.resource import GcsResource
from cook.scheduler import (
    Scheduler,
    TaskOutputError,
    compute_effective_digest,
    is_stale,
)
from cook.store.sqlite import SqliteBuildStore
from cook.task import ShellTask


@pytest.fixture()
def store(tmp_path: Path) -> Generator[SqliteBuildStore]:
    with SqliteBuildStore(tmp_path / ".cook.db") as s:
        yield s


@pytest.fixture()
def executor() -> LocalExecutor:
    return LocalExecutor(max_concurrent=4)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_gcs_input_triggers_build(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """A task with a gs:// input runs when the input exists."""
    gcs_bucket.blob("src/data.txt").upload_from_string(b"input data")

    outfile = tmp_path / "output.txt"
    task = ShellTask(
        name="process",
        cmd=f"echo processed > {outfile}",
        inputs=[f"gs://{gcs_bucket.name}/src/data.txt"],
        outputs=[str(outfile)],
    )

    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])

    assert outfile.exists()
    assert outfile.read_text().strip() == "processed"


async def test_gcs_input_change_triggers_rebuild(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """Changing a gs:// input invalidates the task digest."""
    gcs_bucket.blob("src/data.txt").upload_from_string(b"version1")

    outfile = tmp_path / "output.txt"
    task = ShellTask(
        name="process",
        cmd=f"echo built > {outfile}",
        inputs=[f"gs://{gcs_bucket.name}/src/data.txt"],
        outputs=[str(outfile)],
    )

    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])

    d1 = compute_effective_digest(task, store, project_root=tmp_path)

    gcs_bucket.blob("src/data.txt").upload_from_string(b"version2")

    d2 = compute_effective_digest(task, store, project_root=tmp_path)
    assert d1 != d2


async def test_gcs_input_unchanged_is_fresh(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """A task with unchanged gs:// input is fresh on second run."""
    gcs_bucket.blob("src/data.txt").upload_from_string(b"stable content")

    outfile = tmp_path / "output.txt"
    task = ShellTask(
        name="process",
        cmd=f"echo built > {outfile}",
        inputs=[f"gs://{gcs_bucket.name}/src/data.txt"],
        outputs=[str(outfile)],
    )

    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])
    assert not is_stale(task, store, project_root=tmp_path)

    sched2 = Scheduler(store, executor, project_root=tmp_path)
    await sched2.run([task])
    assert sched2._fresh == 1
    assert sched2._cooked == 0


async def test_gcs_output_verified(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """A task declaring a gs:// output that doesn't get created fails."""
    task = ShellTask(
        name="bad-upload",
        cmd="true",
        outputs=[f"gs://{gcs_bucket.name}/outputs/result.txt"],
    )

    sched = Scheduler(store, executor, project_root=tmp_path)
    with pytest.raises(TaskOutputError) as exc_info:
        await sched.run([task])
    assert "bad-upload" in str(exc_info.value)
    assert f"gs://{gcs_bucket.name}/outputs/result.txt" in str(exc_info.value)


async def test_gcs_output_exists_is_fresh(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """A task with a gs:// output that exists is fresh after first run."""
    gcs_bucket.blob("outputs/result.txt").upload_from_string(b"result")
    gs_out = f"gs://{gcs_bucket.name}/outputs/result.txt"

    task = ShellTask(name="upload", cmd="true", outputs=[gs_out])

    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])
    assert not is_stale(task, store, project_root=tmp_path)


async def test_mixed_local_and_gcs_inputs(
    tmp_path: Path,
    gcs_bucket,  # type: ignore[no-untyped-def]
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """A task can mix local file inputs with gs:// inputs."""
    local_input = tmp_path / "local.txt"
    local_input.write_text("local data")

    gcs_bucket.blob("remote.txt").upload_from_string(b"remote data")

    outfile = tmp_path / "combined.txt"
    task = ShellTask(
        name="combine",
        cmd=f"echo combined > {outfile}",
        inputs=[str(local_input), f"gs://{gcs_bucket.name}/remote.txt"],
        outputs=[str(outfile)],
    )

    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])
    assert outfile.exists()
    assert not is_stale(task, store, project_root=tmp_path)

    gcs_bucket.blob("remote.txt").upload_from_string(b"remote data v2")
    assert is_stale(task, store, project_root=tmp_path)


def test_gcs_resource_exists_e2e(
    gcs_bucket,  # type: ignore[no-untyped-def]
) -> None:
    """GcsResource.exists() works end-to-end with fake-gcs-server."""
    r = GcsResource(bucket=gcs_bucket.name, object_key="does-not-exist.txt")
    assert not r.exists()

    gcs_bucket.blob("now-exists.txt").upload_from_string(b"hi")
    r2 = GcsResource(bucket=gcs_bucket.name, object_key="now-exists.txt")
    assert r2.exists()


def test_gcs_resource_digest_e2e(
    gcs_bucket,  # type: ignore[no-untyped-def]
) -> None:
    """GcsResource.digest() returns correct MD5 end-to-end."""
    import hashlib

    content = b"deterministic content for hashing"
    gcs_bucket.blob("hashme.txt").upload_from_string(content)

    r = GcsResource(bucket=gcs_bucket.name, object_key="hashme.txt")
    assert r.digest() == hashlib.md5(content).digest()
