from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from cook.executor import LocalExecutor
from cook.scheduler import (
    BuildError,
    DependencyFailedError,
    Scheduler,
    TaskOutputError,
    compute_effective_digest,
    is_stale,
)
from cook.store import TaskRecord
from cook.store.sqlite import SqliteBuildStore
from cook.task import ShellTask


@pytest.fixture
def store(tmp_path: Path) -> Generator[SqliteBuildStore]:
    s = SqliteBuildStore(tmp_path / ".cook.db")
    yield s
    s.close()


@pytest.fixture
def executor() -> LocalExecutor:
    return LocalExecutor(max_concurrent=4)


async def test_basic_execution(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="basic",
        cmd=f"echo hello > {outfile}",
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert outfile.read_text().strip() == "hello"


async def test_dependency_ordering(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    task_b = ShellTask(
        name="B",
        cmd=f"echo B > {file_b}",
        outputs=[str(file_b)],
    )
    task_a = ShellTask(
        name="A",
        cmd=f"cat {file_b} > {file_a} && echo A >> {file_a}",
        inputs=[task_b, str(file_b)],
        outputs=[str(file_a)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task_a])
    assert "B" in file_a.read_text()
    assert "A" in file_a.read_text()


async def test_skipping_up_to_date(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    infile = tmp_path / "in.txt"
    infile.write_text("input")
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="skip-test",
        cmd=f"cat {infile} > {outfile}",
        inputs=[str(infile)],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert "Cooked" in capsys.readouterr().err

    # Run again — should skip
    sched2 = Scheduler(store, executor)
    await sched2.run([task])
    captured = capsys.readouterr().err
    assert "Fresh" in captured
    assert "Cooked" not in captured


async def test_staleness_on_input_change(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    infile = tmp_path / "src.txt"
    infile.write_text("v1")
    outfile = tmp_path / "dst.txt"
    task = ShellTask(
        name="stale-input",
        cmd=f"cat {infile} > {outfile}",
        inputs=[str(infile)],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert outfile.read_text().strip() == "v1"

    # Modify input
    infile.write_text("v2")
    sched2 = Scheduler(store, executor)
    await sched2.run([task])
    assert outfile.read_text().strip() == "v2"
    captured = capsys.readouterr().err
    assert "Cooked" in captured


async def test_staleness_on_command_change(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outfile = tmp_path / "out.txt"
    task1 = ShellTask(
        name="cmd-change",
        cmd=f"echo v1 > {outfile}",
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task1])
    assert outfile.read_text().strip() == "v1"

    task2 = ShellTask(
        name="cmd-change",
        cmd=f"echo v2 > {outfile}",
        outputs=[str(outfile)],
    )
    sched2 = Scheduler(store, executor)
    await sched2.run([task2])
    assert outfile.read_text().strip() == "v2"
    assert "Cooked" in capsys.readouterr().err


async def test_missing_output_forces_rebuild(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="missing-out",
        cmd=f"echo rebuilt > {outfile}",
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])

    # Delete output
    outfile.unlink()
    sched2 = Scheduler(store, executor)
    await sched2.run([task])
    captured = capsys.readouterr().err
    assert "Cooked" in captured
    assert outfile.exists()


async def test_no_output_tasks_always_run(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = tmp_path / "count.txt"
    marker.write_text("0")
    task = ShellTask(
        name="no-outputs",
        cmd=f"echo $(( $(cat {marker}) + 1 )) > {marker}",
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    await sched.run([task])  # uses cached future from first run

    # New scheduler to clear dedup cache
    sched2 = Scheduler(store, executor)
    await sched2.run([task])
    captured = capsys.readouterr().err
    assert captured.count("Cooked") >= 2


async def test_none_propagation(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A has no outputs => always run, effective digest is None
    task_a = ShellTask(name="always-run", cmd="true")
    outfile = tmp_path / "b.txt"
    task_b = ShellTask(
        name="downstream",
        cmd=f"echo ok > {outfile}",
        inputs=[task_a],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task_b])

    sched2 = Scheduler(store, executor)
    await sched2.run([task_b])
    captured = capsys.readouterr().err
    # Both A and B should run both times (None propagation)
    assert captured.count("downstream") >= 2


async def test_output_validation(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    missing = tmp_path / "does_not_exist.txt"
    task = ShellTask(
        name="bad-output",
        cmd="true",
        outputs=[str(missing)],
    )
    sched = Scheduler(store, executor)
    with pytest.raises(TaskOutputError) as exc_info:
        await sched.run([task])
    assert task is exc_info.value.task
    assert len(exc_info.value.missing) == 1


async def test_deduplication_diamond(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    counter = tmp_path / "counter.txt"
    counter.write_text("0")
    # D is shared dep of B and C; A depends on B and C
    task_d = ShellTask(
        name="D",
        cmd=f"echo $(( $(cat {counter}) + 1 )) > {counter}",
        outputs=[str(counter)],
    )
    task_b = ShellTask(
        name="B",
        cmd=f"touch {tmp_path / 'b.txt'}",
        inputs=[task_d],
        outputs=[str(tmp_path / "b.txt")],
    )
    task_c = ShellTask(
        name="C",
        cmd=f"touch {tmp_path / 'c.txt'}",
        inputs=[task_d],
        outputs=[str(tmp_path / "c.txt")],
    )
    task_a = ShellTask(
        name="A",
        cmd=f"touch {tmp_path / 'a.txt'}",
        inputs=[task_b, task_c],
        outputs=[str(tmp_path / "a.txt")],
    )
    sched = Scheduler(store, executor)
    await sched.run([task_a])
    captured = capsys.readouterr().err
    # D should only be cooked once
    assert captured.count("Cooked  D") == 1
    # Counter should be 1 (only incremented once)
    assert counter.read_text().strip() == "1"


async def test_keep_going_independent_tasks(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    good_out = tmp_path / "good.txt"
    task_fail = ShellTask(
        name="fail-task", cmd="exit 1", outputs=[str(tmp_path / "nope.txt")]
    )
    task_good = ShellTask(
        name="good-task",
        cmd=f"sleep 0.1 && echo ok > {good_out}",
        outputs=[str(good_out)],
    )
    sched = Scheduler(store, executor, keep_going=True)
    with pytest.raises(BuildError) as exc_info:
        await sched.run([task_fail, task_good])
    # Good task still ran despite the failure being observed first
    assert good_out.exists()
    assert len(exc_info.value.failures) >= 1


async def test_dependency_failure_skips_dependent(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    task_fail = ShellTask(
        name="dep-fail", cmd="exit 1", outputs=[str(tmp_path / "x.txt")]
    )
    task_dep = ShellTask(
        name="dependent",
        cmd="echo should-not-run",
        inputs=[task_fail],
        outputs=[str(tmp_path / "y.txt")],
    )
    sched = Scheduler(store, executor, keep_going=True)
    with pytest.raises(BuildError):
        await sched.run([task_dep])
    assert not (tmp_path / "y.txt").exists()


async def test_intermediate_file_hashing(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    intermediate = tmp_path / "inter.txt"
    final = tmp_path / "final.txt"

    task_a = ShellTask(
        name="produce",
        cmd=f"echo v1 > {intermediate}",
        outputs=[str(intermediate)],
    )
    task_b = ShellTask(
        name="consume",
        cmd=f"cat {intermediate} > {final}",
        inputs=[task_a, str(intermediate)],
        outputs=[str(final)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task_b])
    assert final.read_text().strip() == "v1"

    # Change A's command to produce different content
    task_a2 = ShellTask(
        name="produce",
        cmd=f"echo v2 > {intermediate}",
        outputs=[str(intermediate)],
    )
    task_b2 = ShellTask(
        name="consume",
        cmd=f"cat {intermediate} > {final}",
        inputs=[task_a2, str(intermediate)],
        outputs=[str(final)],
    )
    sched2 = Scheduler(store, executor)
    await sched2.run([task_b2])
    assert final.read_text().strip() == "v2"
    captured = capsys.readouterr().err
    assert "Cooked  consume" in captured


async def test_parallel_execution(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    # Two independent tasks should run concurrently
    out_a = tmp_path / "pa.txt"
    out_b = tmp_path / "pb.txt"
    task_a = ShellTask(
        name="par-a",
        cmd=f"sleep 0.1 && echo a > {out_a}",
        outputs=[str(out_a)],
    )
    task_b = ShellTask(
        name="par-b",
        cmd=f"sleep 0.1 && echo b > {out_b}",
        outputs=[str(out_b)],
    )
    import time

    start = time.monotonic()
    sched = Scheduler(store, executor)
    await sched.run([task_a, task_b])
    elapsed = time.monotonic() - start
    # If parallel, should take ~0.1s, not ~0.2s
    assert elapsed < 0.3
    assert out_a.exists()
    assert out_b.exists()


async def test_dependency_failure_without_keep_going(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    from cook.executor import TaskExecutionError

    task_fail = ShellTask(
        name="fail-nok", cmd="exit 1", outputs=[str(tmp_path / "x.txt")]
    )
    task_dep = ShellTask(
        name="dep-nok",
        cmd="echo nope",
        inputs=[task_fail],
        outputs=[str(tmp_path / "y.txt")],
    )
    sched = Scheduler(store, executor, keep_going=False)
    with pytest.raises(TaskExecutionError):
        await sched.run([task_dep])


async def test_build_error_message(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    task_fail = ShellTask(
        name="err-msg", cmd="exit 1", outputs=[str(tmp_path / "x.txt")]
    )
    sched = Scheduler(store, executor, keep_going=True)
    with pytest.raises(BuildError) as exc_info:
        await sched.run([task_fail])
    assert "err-msg" in str(exc_info.value)


async def test_task_output_error_attributes(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    missing = tmp_path / "ghost.txt"
    task = ShellTask(name="ghost", cmd="true", outputs=[str(missing)])
    sched = Scheduler(store, executor)
    with pytest.raises(TaskOutputError) as exc_info:
        await sched.run([task])
    assert "ghost" in str(exc_info.value)
    assert exc_info.value.task.name == "ghost"


async def test_build_error_with_generic_exception() -> None:
    """BuildError formats non-task exceptions via str()."""
    err = BuildError([ValueError("generic")])
    assert "generic" in str(err)


async def test_dep_with_outputs_but_no_record(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A (no outputs) -> B (has outputs) -> C (has outputs).
    B's effective digest is None (propagated from A), so no record is stored.
    C should also get None effective digest because B has no record."""
    task_a = ShellTask(name="no-out-root", cmd="true")
    b_out = tmp_path / "b.txt"
    task_b = ShellTask(
        name="mid-with-out",
        cmd=f"echo b > {b_out}",
        inputs=[task_a],
        outputs=[str(b_out)],
    )
    c_out = tmp_path / "c.txt"
    task_c = ShellTask(
        name="end-with-out",
        cmd=f"echo c > {c_out}",
        inputs=[task_b],
        outputs=[str(c_out)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task_c])
    captured = capsys.readouterr().err
    # All three should have run (none skipped due to None propagation)
    assert "no-out-root" in captured
    assert "mid-with-out" in captured
    assert "end-with-out" in captured
    # B has a run record but no digest (None effective digest)
    record = store.get("mid-with-out")
    assert record is not None
    assert record.digest is None


async def test_dependency_failed_error_attributes(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    task_fail = ShellTask(
        name="root-fail", cmd="exit 1", outputs=[str(tmp_path / "x.txt")]
    )
    task_dep = ShellTask(
        name="child",
        cmd="true",
        inputs=[task_fail],
        outputs=[str(tmp_path / "y.txt")],
    )
    sched = Scheduler(store, executor, keep_going=True)
    with pytest.raises(BuildError) as exc_info:
        await sched.run([task_dep])
    dep_errors = [
        e for e in exc_info.value.failures if isinstance(e, DependencyFailedError)
    ]
    assert len(dep_errors) >= 1
    assert dep_errors[0].failed_dep == "root-fail"


async def test_independent_failure_no_deadlock(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Two independent targets, one fails — the other should still complete
    (not be cancelled or deadlocked). Regression test for subprocess
    cancellation deadlock with semaphore concurrency limits."""
    from cook.executor import TaskExecutionError

    good_out = tmp_path / "good.txt"
    task_fail = ShellTask(
        name="ind-fail", cmd="exit 1", outputs=[str(tmp_path / "nope.txt")]
    )
    task_good = ShellTask(
        name="ind-good",
        cmd=f"echo ok > {good_out}",
        outputs=[str(good_out)],
    )
    # Use max_concurrent=1 to maximize deadlock risk
    limited_executor = LocalExecutor(max_concurrent=1)
    sched = Scheduler(store, limited_executor, keep_going=False)
    with pytest.raises(TaskExecutionError):
        await sched.run([task_fail, task_good])
    # Good task should have completed (not cancelled)
    assert good_out.exists()


async def test_missing_file_input_clear_error(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="bad-input",
        cmd=f"echo x > {outfile}",
        inputs=[str(tmp_path / "nonexistent.txt")],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    with pytest.raises(FileNotFoundError, match="nonexistent.txt") as exc_info:
        await sched.run([task])
    assert exc_info.value.__cause__ is not None


async def test_implicit_file_dep_ordering(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Scheduler respects implicit deps populated via _deps (as resolve_file_deps does)."""
    file_a = tmp_path / "a.txt"
    file_b = tmp_path / "b.txt"
    task_a = ShellTask(
        name="produce",
        cmd=f"echo produced > {file_a}",
        outputs=[str(file_a)],
    )
    task_b = ShellTask(
        name="consume",
        cmd=f"cat {file_a} > {file_b}",
        inputs=[str(file_a)],
        outputs=[str(file_b)],
    )
    # Simulate what resolve_file_deps does: add implicit dep via _deps
    task_b._deps.add(task_a)
    sched = Scheduler(store, executor)
    await sched.run([task_b])
    assert file_a.read_text().strip() == "produced"
    assert file_b.read_text().strip() == "produced"


# --- is_stale() unit tests ---


def test_is_stale_no_outputs(store: SqliteBuildStore) -> None:
    """Tasks with no outputs are always stale."""
    task = ShellTask(name="always", cmd="true")
    assert is_stale(task, store) is True


def test_is_stale_no_record(tmp_path: Path, store: SqliteBuildStore) -> None:
    """Task with outputs but no store record is stale."""
    outfile = tmp_path / "out.txt"
    outfile.write_text("exists")
    task = ShellTask(name="no-rec", cmd="true", outputs=[str(outfile)])
    assert is_stale(task, store) is True


async def test_is_stale_up_to_date(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """After a successful build, task is not stale."""
    infile = tmp_path / "in.txt"
    infile.write_text("data")
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="fresh",
        cmd=f"cat {infile} > {outfile}",
        inputs=[str(infile)],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert is_stale(task, store) is False


async def test_is_stale_after_input_change(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Modifying a file input makes the task stale."""
    infile = tmp_path / "in.txt"
    infile.write_text("v1")
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="input-change",
        cmd=f"cat {infile} > {outfile}",
        inputs=[str(infile)],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert is_stale(task, store) is False

    infile.write_text("v2")
    assert is_stale(task, store) is True


async def test_is_stale_missing_output(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Deleting an output file makes the task stale."""
    outfile = tmp_path / "out.txt"
    task = ShellTask(
        name="del-out",
        cmd=f"echo ok > {outfile}",
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert is_stale(task, store) is False

    outfile.unlink()
    assert is_stale(task, store) is True


def test_is_stale_dep_no_outputs(tmp_path: Path, store: SqliteBuildStore) -> None:
    """A dep with no outputs makes the parent stale (recursive)."""
    always_run = ShellTask(name="always-dep", cmd="true")
    outfile = tmp_path / "out.txt"
    outfile.write_text("exists")
    task = ShellTask(
        name="has-dep",
        cmd="true",
        inputs=[always_run],
        outputs=[str(outfile)],
    )
    assert is_stale(task, store) is True


async def test_is_stale_dep_stale_propagates(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """If a dep is stale, the dependent task is also stale."""
    infile = tmp_path / "src.txt"
    infile.write_text("v1")
    mid = tmp_path / "mid.txt"
    final = tmp_path / "final.txt"
    dep = ShellTask(
        name="dep-task",
        cmd=f"cat {infile} > {mid}",
        inputs=[str(infile)],
        outputs=[str(mid)],
    )
    task = ShellTask(
        name="main-task",
        cmd=f"cat {mid} > {final}",
        inputs=[dep, str(mid)],
        outputs=[str(final)],
    )
    sched = Scheduler(store, executor)
    await sched.run([task])
    assert is_stale(task, store) is False

    infile.write_text("v2")
    assert is_stale(dep, store) is True
    assert is_stale(task, store) is True


async def test_scheduler_records_last_failed(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """Scheduler should store a record with last_failed when a task fails."""
    from cook.executor import TaskExecutionError

    task = ShellTask(
        name="fail-record",
        cmd="echo oops >&2; exit 1",
        outputs=[str(tmp_path / "nope.txt")],
    )
    sched = Scheduler(store, executor)
    with pytest.raises(TaskExecutionError):
        await sched.run([task])

    record = store.get("fail-record")
    assert record is not None
    assert record.last_failed is not None
    assert record.last_started is not None
    assert record.last_succeeded is None
    assert record.error is not None
    assert "oops" in record.error


async def test_input_modified_during_build_detected_as_stale(
    tmp_path: Path,
    store: SqliteBuildStore,
    executor: LocalExecutor,
) -> None:
    """If an input file is modified while the task runs, the next build
    should detect staleness because the stored digest no longer matches."""
    infile = tmp_path / "src.txt"
    infile.write_text("v1")
    outfile = tmp_path / "out.txt"
    # Task reads infile, but also sleeps briefly so we can modify infile mid-build
    task = ShellTask(
        name="racy",
        cmd=f"cat {infile} > {outfile} && sleep 0.2",
        inputs=[str(infile)],
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor)
    import asyncio

    run_fut = asyncio.ensure_future(sched.run([task]))
    await asyncio.sleep(0.1)
    # Modify input while task is running
    infile.write_text("v2")
    await run_fut

    # Output was created with v1 content
    assert outfile.read_text().strip() == "v1"
    # But the stored digest was computed with v1 content in infile.
    # Now infile has v2, so the task should be stale.
    assert is_stale(task, store) is True


async def test_cancel_stops_scheduler(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Cancelling the scheduler run stops execution of subsequent tasks."""
    import asyncio

    marker = tmp_path / "marker.txt"
    # Task A runs quickly, Task B sleeps forever (would hang if not cancelled)
    task_a = ShellTask(
        name="quick",
        cmd=f"echo done > {marker}",
        outputs=[str(marker)],
    )
    task_b = ShellTask(
        name="slow",
        cmd="sleep 60",
        inputs=[task_a],
        outputs=[str(tmp_path / "never.txt")],
    )
    sched = Scheduler(store, executor)
    t = asyncio.ensure_future(sched.run([task_b]))
    # Wait for task A to finish, then cancel before B completes
    for _ in range(50):
        await asyncio.sleep(0.05)
        if marker.exists():
            break
    t.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t
    # Task A completed, task B's output was never created
    assert marker.exists()
    assert not (tmp_path / "never.txt").exists()


def test_is_stale_missing_file_input(tmp_path: Path, store: SqliteBuildStore) -> None:
    """Missing file input means the task is stale, not an error."""
    outfile = tmp_path / "out.txt"
    outfile.write_text("exists")
    task = ShellTask(
        name="missing-input",
        cmd="true",
        inputs=[str(tmp_path / "gone.txt")],
        outputs=[str(outfile)],
    )
    assert is_stale(task, store) is True


def test_is_stale_memoizes_diamond(tmp_path: Path, store: SqliteBuildStore) -> None:
    """is_stale memoizes results so diamond DAGs don't re-check shared deps."""
    shared_out = tmp_path / "shared.txt"
    shared_out.write_text("data")
    shared = ShellTask(name="shared", cmd="true", outputs=[str(shared_out)])

    left_out = tmp_path / "left.txt"
    left_out.write_text("left")
    left = ShellTask(name="left", cmd="true", inputs=[shared], outputs=[str(left_out)])

    right_out = tmp_path / "right.txt"
    right_out.write_text("right")
    right = ShellTask(
        name="right", cmd="true", inputs=[shared], outputs=[str(right_out)]
    )

    # Store records so everything is up-to-date
    shared_digest = compute_effective_digest(shared, store)
    assert shared_digest is not None
    store.save(TaskRecord(task_id="shared", digest=shared_digest))

    left_digest = compute_effective_digest(left, store)
    assert left_digest is not None
    store.save(TaskRecord(task_id="left", digest=left_digest))

    right_digest = compute_effective_digest(right, store)
    assert right_digest is not None
    store.save(TaskRecord(task_id="right", digest=right_digest))

    # Check both via shared memo dict — shared should be checked only once
    memo: dict[str, bool] = {}
    assert is_stale(left, store, _memo=memo) is False
    assert "shared" in memo  # memoized
    assert is_stale(right, store, _memo=memo) is False


async def test_scheduler_resolves_absolute_output(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Absolute output paths should be resolved correctly."""
    outfile = tmp_path / "abs_out.txt"
    task = ShellTask(
        name="abs",
        cmd=f"echo ok > {outfile}",
        outputs=[str(outfile)],
    )
    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])
    assert outfile.exists()


async def test_scheduler_resolves_relative_output(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Relative output paths should resolve relative to project_root."""
    task = ShellTask(
        name="rel",
        cmd=f"echo ok > {tmp_path / 'rel_out.txt'}",
        outputs=["rel_out.txt"],
    )
    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])
    assert (tmp_path / "rel_out.txt").exists()


async def test_cleanup_session_on_scheduler_exit(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Scheduler.run() cleans up pending/running runs on exit."""
    outfile = tmp_path / "out.txt"
    task = ShellTask(name="t", cmd=f"echo ok > {outfile}", outputs=[str(outfile)])
    sched = Scheduler(store, executor, project_root=tmp_path)
    await sched.run([task])

    # After normal completion, no pending or running records should remain
    assert store.get_running("t") is None
    record = store.get("t")
    assert record is not None
    assert record.digest is not None


async def test_cleanup_marks_running_as_interrupted(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Running tasks are marked as interrupted on cleanup."""
    from datetime import datetime, timezone

    # Simulate: a task started running but the session was interrupted
    session_id = "test-session"
    run_id = store.start_run("t", session_id, 1, datetime.now(timezone.utc))
    store.update_run_status(run_id, "running")

    store.cleanup_session(session_id)

    assert store.get_running("t") is None
    record = store.get("t")
    assert record is not None
    assert record.error == "interrupted"
    assert record.last_failed is not None


async def test_cleanup_deletes_pending(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Pending tasks are deleted (not marked as failed) on cleanup."""
    from datetime import datetime, timezone

    session_id = "test-session"
    store.start_run("t", session_id, 1, datetime.now(timezone.utc))
    # Status is 'pending' — never transitioned to running

    store.cleanup_session(session_id)

    assert store.get_running("t") is None
    assert store.get("t") is None  # completely gone


async def test_fresh_task_no_pending_residue(
    tmp_path: Path, store: SqliteBuildStore, executor: LocalExecutor
) -> None:
    """Fresh (up-to-date) tasks should not leave pending run records."""
    outfile = tmp_path / "out.txt"
    task = ShellTask(name="t", cmd=f"echo ok > {outfile}", outputs=[str(outfile)])
    sched = Scheduler(store, executor, project_root=tmp_path)

    # First run — executes
    await sched.run([task])
    assert outfile.exists()

    # Count runs
    row = store._conn.execute(
        "SELECT COUNT(*) FROM runs WHERE task_id = 't'"
    ).fetchone()
    runs_after_first = row[0]

    # Second run — should be fresh, no new run record
    sched2 = Scheduler(store, executor, project_root=tmp_path)
    await sched2.run([task])

    row = store._conn.execute(
        "SELECT COUNT(*) FROM runs WHERE task_id = 't'"
    ).fetchone()
    runs_after_second = row[0]

    # Fresh task should not add a new run
    assert runs_after_second == runs_after_first
