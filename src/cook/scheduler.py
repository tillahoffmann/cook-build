from __future__ import annotations

import asyncio
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from .executor import Executor
from .store import BuildStore, FileDigestCache, TaskRecord
from .task import Task
from .ui import Output, Verbosity


class TaskOutputError(Exception):
    def __init__(self, task: Task, missing: list[Path]) -> None:
        self.task = task
        self.missing = missing
        paths = ", ".join(str(p) for p in missing)
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
        names = ", ".join(_task_name_from_error(e) for e in failures)
        super().__init__(f"Build failed. Task errors: {names}")


def _task_name_from_error(e: Exception) -> str:
    from .executor import TaskExecutionError

    if isinstance(e, (TaskOutputError, DependencyFailedError, TaskExecutionError)):
        return e.task.name
    return str(e)


def compute_effective_digest(
    task: Task,
    store: BuildStore,
    file_cache: FileDigestCache | None = None,
) -> str | None:
    """Compute the effective digest for a task given a store.

    Returns None if the task has no outputs or if any dependency
    propagates None (e.g. a dep with no outputs).
    """
    if not task.outputs:
        return None

    dep_digests: list[tuple[str, str]] = []
    for dep in sorted(task.task_deps, key=lambda d: d.name):
        if not dep.outputs:
            return None
        dep_record = store.get(dep.task_id)
        if dep_record is None:
            return None
        dep_digests.append((dep.name, dep_record.digest))

    h = hashlib.sha256()
    h.update(task.digest().encode())

    for fi in task.file_inputs:
        resolved = Path(fi).resolve()
        try:
            content_hash = (
                file_cache.hash_file(resolved)
                if file_cache is not None
                else hashlib.sha256(resolved.read_bytes()).digest()
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Input file '{resolved}' not found for task '{task.name}'"
            )
        h.update(str(resolved).encode())
        h.update(content_hash)

    for _, digest in dep_digests:
        h.update(digest.encode())

    return h.hexdigest()


def is_stale(
    task: Task,
    store: BuildStore,
    file_cache: FileDigestCache | None = None,
    _memo: dict[str, bool] | None = None,
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
    if _memo is None:
        _memo = {}
    if task.task_id in _memo:
        return _memo[task.task_id]

    if not task.outputs:
        _memo[task.task_id] = True
        return True

    for dep in task.task_deps:
        if is_stale(dep, store, file_cache, _memo):
            _memo[task.task_id] = True
            return True

    try:
        effective = compute_effective_digest(task, store, file_cache)
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

    result = not all(Path(o).resolve().exists() for o in task.outputs)
    _memo[task.task_id] = result
    return result


class Scheduler:
    def __init__(
        self,
        store: BuildStore,
        executor: Executor,
        keep_going: bool = False,
        ui: Output | None = None,
        stream: bool = False,
    ) -> None:
        self._store = store
        self._executor = executor
        self._executor.stream = stream
        self._keep_going = keep_going
        self._ui = ui or Output(verbosity=Verbosity.NORMAL)
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
        from .cli.util import collect_transitive

        all_tasks = collect_transitive(targets)
        self._ui.set_total(len(all_tasks))

        build_start = time.monotonic()

        # Always use return_exceptions=True to avoid gather cancelling
        # in-flight subprocess tasks (which can deadlock asyncio.run shutdown).
        results = await asyncio.gather(
            *(self._ensure(t) for t in targets), return_exceptions=True
        )
        for r in results:
            if isinstance(r, Exception):
                if not any(r is e for e in self._errors):
                    self._errors.append(r)

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
                if all(Path(o).resolve().exists() for o in task.outputs):
                    self._fresh += 1
                    self._ui.task_fresh(task.name)
                    return

        # 5. Execute
        started_at = datetime.now(timezone.utc)
        try:
            await self._executor.execute(task)
        except Exception as exc:
            failed_at = datetime.now(timezone.utc)
            elapsed = (failed_at - started_at).total_seconds()
            self._task_failures += 1

            # Build error output from the exception
            from .executor import TaskExecutionError

            output = ""
            if isinstance(exc, TaskExecutionError):
                output = exc.stderr or exc.stdout
            else:
                output = str(exc)

            self._ui.task_failed(task.name, elapsed, output)
            self._failed.add(task.task_id)
            self._store.save(
                TaskRecord(
                    task_id=task.task_id,
                    digest=effective or "",
                    last_started=started_at,
                    last_failed=failed_at,
                    error=str(exc),
                )
            )
            raise

        finished_at = datetime.now(timezone.utc)
        elapsed = (finished_at - started_at).total_seconds()

        # 6. Verify outputs exist
        if task.outputs:
            missing = [
                Path(o).resolve()
                for o in task.outputs
                if not Path(o).resolve().exists()
            ]
            if missing:
                err = TaskOutputError(task, missing)
                self._task_failures += 1
                self._ui.task_failed(task.name, elapsed, str(err))
                self._failed.add(task.task_id)
                self._store.save(
                    TaskRecord(
                        task_id=task.task_id,
                        digest=effective or "",
                        last_started=started_at,
                        last_failed=finished_at,
                        error=str(err),
                    )
                )
                raise err

        self._cooked += 1
        self._ui.task_cooked(task.name, elapsed)

        # 7. Store record
        if effective is not None:
            self._store.save(
                TaskRecord(
                    task_id=task.task_id,
                    digest=effective,
                    last_started=started_at,
                    last_succeeded=finished_at,
                )
            )

    def _compute_effective_digest(self, task: Task) -> str | None:
        return compute_effective_digest(task, self._store, self._file_cache)
