from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..store import TaskRecord
from ..task import Task
from ..ui import Output, Style


def load_recipe(recipe_path: str) -> None:
    path = Path(recipe_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Recipe file not found: {recipe_path}")

    recipe_dir = str(path.parent)
    added = False
    if not sys.path or sys.path[0] != recipe_dir:
        sys.path.insert(0, recipe_dir)
        added = True

    try:
        spec = importlib.util.spec_from_file_location("__cook_recipe__", str(path))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load recipe from {recipe_path}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if added and sys.path and sys.path[0] == recipe_dir:
            sys.path.pop(0)


def match_targets(
    tasks: dict[str, Task],
    patterns: list[str],
    default: str | None,
    use_regex: bool = False,
) -> list[Task]:
    effective_patterns = patterns if patterns else ([default] if default else [])
    if not effective_patterns:
        raise ValueError(
            "No target pattern provided and no default configured in cook.toml. "
            "Specify a pattern: cook exec '<pattern>'"
        )
    seen: set[str] = set()
    matched: list[Task] = []
    for pat in effective_patterns:
        if use_regex:
            try:
                compiled = re.compile(pat)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern {pat!r}: {e}")
            hits = [t for name, t in tasks.items() if compiled.search(name)]
        else:
            hits = [t for name, t in tasks.items() if fnmatch.fnmatch(name, pat)]
        for t in hits:
            if t.name not in seen:
                seen.add(t.name)
                matched.append(t)
    if not matched:
        pat_str = ", ".join(repr(p) for p in effective_patterns)
        available = ", ".join(sorted(tasks.keys()))
        raise ValueError(
            f"Pattern(s) {pat_str} matched no tasks. Available tasks: {available}"
        )
    return matched


def match_outputs(
    tasks: dict[str, Task],
    patterns: list[str],
    use_regex: bool = False,
) -> list[Task]:
    if not patterns:
        raise ValueError(
            "No output pattern provided. Specify a pattern: cook build '<pattern>'"
        )
    seen: set[str] = set()
    matched: list[Task] = []
    for pat in patterns:
        compiled = None
        if use_regex:
            try:
                compiled = re.compile(pat)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern {pat!r}: {e}")

        hits: list[Task] = []
        for task in tasks.values():
            for out in task.outputs:
                out_str = str(out)
                if compiled is not None:
                    is_match = compiled.search(out_str) is not None
                else:
                    is_match = fnmatch.fnmatch(out_str, pat)
                if is_match:
                    hits.append(task)
                    break
        if not hits:
            raise ValueError(
                f"Output pattern {pat!r} matched no task outputs. "
                f"Available outputs: {', '.join(sorted(str(o) for t in tasks.values() for o in t.outputs))}"
            )
        for t in hits:
            if t.name not in seen:
                seen.add(t.name)
                matched.append(t)
    return matched


def run_targets(
    targets: list[Task],
    args: argparse.Namespace,
    config: Config,
    ui: Output,
) -> int:
    """Shared execution logic for exec and build commands."""
    import asyncio
    import sys

    from ..executor import get_executor
    from ..scheduler import Scheduler, is_stale
    from ..store import FileDigestCache
    from ..store.sqlite import SqliteBuildStore
    from ..transform import collect_transitive

    executor_name = args.executor if args.executor is not None else config.executor
    executor_cls = get_executor(executor_name)

    if args.dry_run:
        all_tasks = collect_transitive(targets)
        db_path = Path(".cook.db")
        if db_path.exists():
            cache = FileDigestCache()
            with SqliteBuildStore(str(db_path)) as store:
                for task in all_tasks:
                    stale = is_stale(task, store, cache)
                    status = "STALE (would run)" if stale else "up-to-date"
                    print(f"[{task.name}] {status}", file=sys.stderr)
        else:
            for task in all_tasks:
                print(f"[{task.name}] STALE (would run)", file=sys.stderr)
        return 0

    stream = getattr(args, "stream", False)
    executor_config = config.executor_configs.get(executor_name, {})
    executor = executor_cls.from_config(executor_config, args.jobs)
    with SqliteBuildStore(".cook.db") as store:
        scheduler = Scheduler(
            store, executor, keep_going=args.keep_going, ui=ui, stream=stream
        )
        asyncio.run(scheduler.run(targets))

    return 0


def format_relative_time(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def print_task_detail(
    task: Task, stale: bool, record: TaskRecord | None, ui: Output | None = None
) -> None:
    from ..task import ShellTask

    style = ui.style if ui is not None else Style(False)
    status = style.red("STALE") if stale else style.green("up-to-date")
    print(f"[{task.name}] {status}")

    # Dependencies
    deps = task.task_deps
    if deps:
        print(f"    deps: {', '.join(d.name for d in deps)}")

    # File inputs
    file_inputs = task.file_inputs
    if file_inputs:
        print(f"    inputs: {', '.join(str(f) for f in file_inputs)}")

    # Outputs
    if task.outputs:
        print(f"    outputs: {', '.join(str(o) for o in task.outputs)}")

    # Command (for ShellTask)
    if isinstance(task, ShellTask) and task.cmd:
        cmd_display = task.cmd if len(task.cmd) <= 80 else task.cmd[:77] + "..."
        print(f"    cmd: {cmd_display}")

    # Execution history from store
    if record is not None:
        if record.last_started:
            print(f"    last started: {format_relative_time(record.last_started)}")
        if record.last_succeeded:
            line = f"    last succeeded: {format_relative_time(record.last_succeeded)}"
            if record.last_started and record.last_succeeded >= record.last_started:
                duration = (record.last_succeeded - record.last_started).total_seconds()
                line += f" ({duration:.1f}s)"
            print(line)
        if record.last_failed:
            line = f"    last failed: {format_relative_time(record.last_failed)}"
            if record.last_started and record.last_failed >= record.last_started:
                duration = (record.last_failed - record.last_started).total_seconds()
                line += f" ({duration:.1f}s)"
            print(line)
        if record.error:
            print(f"    error: {record.error}")
