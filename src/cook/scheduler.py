from __future__ import annotations

import asyncio
import hashlib
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .executor import Executor, TaskExecutionError
from .resource import resolve_resource
from .store import BuildStore, FileDigestCache
from .task import ShellTask, Task
from .transform import collect_transitive
from .ui import Output, Verbosity


class TaskOutputError(Exception):
    def __init__(self, task: Task, missing: list[str]) -> None:
        self.task = task
        self.missing = missing
        paths = ", ".join(missing)
        super().__init__(
            f"Task {task.name!r} did not produce expected outputs: {paths}"
        )


class DependencyFailedError(Exception):
    def __init__(self, task: Task, failed_dep: str) -> None:
        self.task = task
        self.failed_dep = failed_dep
        super().__init__(
            f"Task {task.name!r} skipped because dependency {failed_dep!r} failed"
        )


class BuildError(Exception):
    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        # Filter out DependencyFailedError — those are consequences, not root causes
        root_causes = [e for e in failures if not isinstance(e, DependencyFailedError)]
        names = ", ".join(_task_name_from_error(e) for e in root_causes) or ", ".join(
            _task_name_from_error(e) for e in failures
        )
        super().__init__(f"Build failed. Task errors: {names}")


def _task_name_from_error(e: Exception) -> str:
    if isinstance(e, (TaskOutputError, DependencyFailedError, TaskExecutionError)):
        return e.task.name
    return str(e)


def compute_effective_digest(
    task: Task,
    store: BuildStore,
    file_cache: FileDigestCache | None = None,
    project_root: Path | None = None,
) -> str | None:
    """Compute the effective digest for a task given a store.

    Returns None if the task has no outputs or if any dependency
    propagates None (e.g. a dep with no outputs).
    """
    if not task.outputs:
        return None

    root = (project_root or Path.cwd()).resolve()

    dep_digests: list[tuple[str, str]] = []
    for dep in sorted(task.task_deps, key=lambda d: d.name):
        if not dep.outputs:
            return None
        dep_record = store.get(dep.task_id)
        if dep_record is None or dep_record.digest is None:
            return None
        dep_digests.append((dep.name, dep_record.digest))

    h = hashlib.sha256()
    h.update(task.digest().encode())

    for fi in task.file_inputs:
        resource = resolve_resource(fi, root)
        try:
            content_hash = (
                file_cache.hash_resource(resource)
                if file_cache is not None
                else resource.digest()
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Input file '{resource.label}' not found for task '{task.name}'"
            )
        h.update(resource.label.encode())
        h.update(content_hash)

    for _, digest in dep_digests:
        h.update(digest.encode())

    return h.hexdigest()


def is_stale(
    task: Task,
    store: BuildStore,
    file_cache: FileDigestCache | None = None,
    _memo: dict[str, bool] | None = None,
    project_root: Path | None = None,
) -> bool:
    """Return True if the task needs to run.

    A task is stale if:
    - it has no outputs (always-run)
    - any dependency is stale
    - no stored record exists
    - any output file is missing
    - the effective digest doesn't match the stored one

    Results are memoized per task_id to avoid exponential re-checking
    on diamond DAGs.
    """
    root = project_root or Path.cwd()
    if _memo is None:
        _memo = {}
    if task.task_id in _memo:
        return _memo[task.task_id]

    if not task.outputs:
        _memo[task.task_id] = True
        return True

    for dep in task.task_deps:
        if is_stale(dep, store, file_cache, _memo, root):
            _memo[task.task_id] = True
            return True

    try:
        effective = compute_effective_digest(task, store, file_cache, root)
    except FileNotFoundError:
        _memo[task.task_id] = True
        return True
    if effective is None:  # pragma: no cover
        # A dependency has no outputs or no stored record despite passing
        # recursive staleness checks — conservatively treat as stale.
        _memo[task.task_id] = True
        return True

    record = store.get(task.task_id)
    if record is None or record.digest != effective:
        _memo[task.task_id] = True
        return True

    result = not all(resolve_resource(o, root).exists() for o in task.outputs)
    _memo[task.task_id] = result
    return result


def staleness_reason(
    task: Task,
    store: BuildStore,
    file_cache: FileDigestCache | None = None,
    project_root: Path | None = None,
) -> str | None:
    """Return a human-readable reason why a task is stale, or None if up-to-date."""
    root = project_root or Path.cwd()

    if not task.outputs:
        return "always-run (no outputs)"

    stale_deps = [
        dep.name
        for dep in task.task_deps
        if staleness_reason(dep, store, file_cache, root) is not None
    ]
    if stale_deps:
        n = len(stale_deps)
        return f"{n} {'dependency is' if n == 1 else 'dependencies are'} stale"

    record = store.get(task.task_id)
    if record is None:
        return "never run"

    try:
        effective = compute_effective_digest(task, store, file_cache, root)
    except FileNotFoundError:
        missing_inputs = [
            str(f) for f in task.file_inputs if not resolve_resource(f, root).exists()
        ]
        n = len(missing_inputs)
        return f"{n} {'input is' if n == 1 else 'inputs are'} missing"
    if effective is None:
        return "always-run dependency"  # pragma: no cover

    if record.digest != effective:
        return "digest changed"

    missing_outputs = [
        o for o in task.outputs if not resolve_resource(o, root).exists()
    ]
    if missing_outputs:
        n = len(missing_outputs)
        return f"{n} {'output is' if n == 1 else 'outputs are'} missing"

    return None


class Scheduler:
    def __init__(
        self,
        store: BuildStore,
        executor: Executor,
        keep_going: bool = False,
        ui: Output | None = None,
        stream: bool = False,
        project_root: Path | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._executor.stream = stream
        self._keep_going = keep_going
        self._ui = ui or Output(verbosity=Verbosity.NORMAL)
        self._project_root = (project_root or Path.cwd()).resolve()
        self._session_id = uuid.uuid4().hex
        self._pid = os.getpid()
        self._futures: dict[str, asyncio.Future[None]] = {}
        self._failed: set[str] = set()
        self._errors: list[Exception] = []
        self._file_cache = FileDigestCache()
        # Counters for summary
        self._cooked = 0
        self._fresh = 0
        self._skipped = 0
        self._task_failures = 0

    async def run(self, targets: list[Task]) -> None:
        self._futures = {}
        self._failed = set()
        self._errors = []
        self._cooked = 0
        self._fresh = 0
        self._skipped = 0
        self._task_failures = 0

        # Count total tasks for progress counter
        all_tasks = collect_transitive(targets)
        self._ui.set_total(len(all_tasks))

        build_start = time.monotonic()

        try:
            # Always use return_exceptions=True to avoid gather cancelling
            # in-flight subprocess tasks (which can deadlock asyncio.run shutdown).
            results = await asyncio.gather(
                *(self._ensure(t) for t in targets), return_exceptions=True
            )
            for r in results:
                if isinstance(r, Exception):
                    if not any(r is e for e in self._errors):
                        self._errors.append(r)
        finally:
            # Clean up any still-running records for this session
            self._store.cleanup_session(self._session_id)

        build_elapsed = time.monotonic() - build_start
        self._ui.summary(
            cooked=self._cooked,
            fresh=self._fresh,
            failed=self._task_failures,
            skipped=self._skipped,
            elapsed=build_elapsed,
        )

        if self._errors:
            if self._keep_going:
                raise BuildError(self._errors)
            raise self._errors[0]

    async def _ensure(self, task: Task) -> None:
        if task.task_id in self._futures:
            return await self._futures[task.task_id]

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._futures[task.task_id] = fut

        try:
            await self._run_task(task)
            fut.set_result(None)
        except BaseException as exc:
            # Must catch BaseException (not Exception) so CancelledError
            # also resolves the future — otherwise gather cancellation
            # leaves unresolved futures that can deadlock the event loop.
            fut.set_exception(exc)
            fut.exception()
            raise

    async def _run_task(self, task: Task) -> None:
        # 0. Mark as pending (waiting for deps)
        pending_at = datetime.now(timezone.utc)
        run_id = self._store.start_run(
            task.task_id, self._session_id, self._pid, pending_at
        )

        # 1. Ensure all dependencies (always return_exceptions to avoid
        #    cancelling in-flight subprocesses)
        deps = task.task_deps
        dep_failed = False
        if deps:
            results = await asyncio.gather(
                *(self._ensure(dep) for dep in deps),
                return_exceptions=True,
            )
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    self._failed.add(deps[i].task_id)
                    dep_failed = True
                    if not any(r is e for e in self._errors):
                        self._errors.append(r)

        # 2. Check for failed dependencies
        if dep_failed:
            self._store.finish_run(
                run_id,
                "failed",
                datetime.now(timezone.utc),
                error="dependency failed",
            )
            if self._keep_going:
                failed_dep = next(d for d in deps if d.task_id in self._failed)
                self._failed.add(task.task_id)
                self._skipped += 1
                self._ui.task_skipped(task.name, failed_dep.task_id)
                err = DependencyFailedError(task, failed_dep.task_id)
                self._errors.append(err)
                raise err
            raise self._errors[0]

        # 3. Compute effective digest AFTER deps complete
        effective = self._compute_effective_digest(task)

        # 4. Staleness check
        if effective is not None:
            record = self._store.get(task.task_id)
            if record is not None and record.digest == effective:
                if all(
                    resolve_resource(o, self._project_root).exists()
                    for o in task.outputs
                ):
                    self._fresh += 1
                    self._ui.task_fresh(task.name)
                    # Task is fresh — discard the pending run
                    self._store.delete_run(run_id)
                    return

        # 5. Execute (transition to running happens when semaphore is acquired)
        if isinstance(task, ShellTask):
            self._ui.verbose(f"  $ {task.cmd}")
        started_at = datetime.now(timezone.utc)

        def on_start() -> None:
            nonlocal started_at
            started_at = datetime.now(timezone.utc)
            self._store.update_run_status(run_id, "running")

        try:
            await self._executor.execute(task, on_start=on_start)
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            elapsed = (failed_at - started_at).total_seconds()
            self._task_failures += 1

            self._ui.task_failed(task.name, elapsed, str(exc))
            self._failed.add(task.task_id)
            self._store.finish_run(run_id, "failed", failed_at, error=str(exc))
            raise

        finished_at = datetime.now(timezone.utc)
        elapsed = (finished_at - started_at).total_seconds()

        # 6. Verify outputs exist
        if task.outputs:
            resources = [resolve_resource(o, self._project_root) for o in task.outputs]
            missing = [r.label for r in resources if not r.exists()]
            if missing:
                err = TaskOutputError(task, missing)
                self._task_failures += 1
                self._ui.task_failed(task.name, elapsed, str(err))
                self._failed.add(task.task_id)
                self._store.finish_run(run_id, "failed", finished_at, error=str(err))
                raise err

        self._cooked += 1
        self._ui.task_cooked(task.name, elapsed)

        # 7. Store record
        self._store.finish_run(run_id, "succeeded", finished_at, digest=effective)

    def _compute_effective_digest(self, task: Task) -> str | None:
        return compute_effective_digest(
            task, self._store, self._file_cache, self._project_root
        )
