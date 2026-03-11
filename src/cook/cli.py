from __future__ import annotations

import argparse
import asyncio
import fnmatch
import importlib.util
import sys
from pathlib import Path

from cook.config import ConfigError, load_config
from cook.context import Context, get_context
from cook.executor import LocalExecutor, TaskExecutionError
from cook.scheduler import (
    BuildError,
    Scheduler,
    TaskOutputError,
    compute_effective_digest,
)
from cook.store.sqlite import SqliteBuildStore
from cook.task import Task


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cook", description="Cook build system")
    sub = parser.add_subparsers(dest="command")

    exec_p = sub.add_parser("exec", help="Run tasks matching a pattern")
    exec_p.add_argument("pattern", nargs="?", default=None)
    exec_p.add_argument("--dry-run", action="store_true", help="Show what would run")
    exec_p.add_argument(
        "-k", "--keep-going", action="store_true", help="Continue on failure"
    )
    exec_p.add_argument("--executor", default=None, help="Override executor")

    inspect_p = sub.add_parser("inspect", help="Show dependency graph and staleness")
    inspect_p.add_argument("pattern", nargs="?", default=None)

    invalidate_p = sub.add_parser("invalidate", help="Invalidate stored digests")
    invalidate_p.add_argument("pattern", nargs="?", default=None)

    return parser


def _load_recipe(recipe_path: str) -> None:
    path = Path(recipe_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Recipe file not found: {recipe_path}")

    recipe_dir = str(path.parent)
    if sys.path and sys.path[0] != recipe_dir:
        sys.path.insert(0, recipe_dir)
    elif not sys.path:
        sys.path.insert(0, recipe_dir)

    spec = importlib.util.spec_from_file_location("__cook_recipe__", str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load recipe from {recipe_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)


def _match_targets(
    tasks: dict[str, Task], pattern: str | None, default: str | None
) -> list[Task]:
    effective_pattern = pattern if pattern is not None else default
    if effective_pattern is None:
        raise ValueError(
            "No target pattern provided and no default configured in cook.toml. "
            "Specify a pattern: cook exec '<pattern>'"
        )
    matched = [
        t for name, t in tasks.items() if fnmatch.fnmatch(name, effective_pattern)
    ]
    if not matched:
        available = ", ".join(sorted(tasks.keys()))
        raise ValueError(
            f"Pattern {effective_pattern!r} matched no tasks. "
            f"Available tasks: {available}"
        )
    return matched


def _collect_transitive(tasks: list[Task]) -> list[Task]:
    visited: set[str] = set()
    result: list[Task] = []

    def walk(task: Task) -> None:
        if task.name in visited:
            return
        visited.add(task.name)
        for dep in task.task_deps:
            walk(dep)
        result.append(task)

    for t in tasks:
        walk(t)
    return result


def _compute_staleness(
    task: Task, store: SqliteBuildStore, seen: dict[str, bool]
) -> bool:
    """Return True if the task is stale (needs to run)."""
    if task.name in seen:
        return seen[task.name]

    # No outputs => always run
    if not task.outputs:
        seen[task.name] = True
        return True

    # Check deps first
    for dep in task.task_deps:
        if _compute_staleness(dep, store, seen):
            seen[task.name] = True
            return True

    # Check stored digest exists
    record = store.get(task.task_id)
    if record is None:
        seen[task.name] = True
        return True

    # Check outputs exist
    if not all(Path(o).resolve().exists() for o in task.outputs):
        seen[task.name] = True
        return True

    # Compute effective digest and compare with stored one
    effective = compute_effective_digest(task, store)
    if effective is None or effective != record.digest:
        seen[task.name] = True
        return True

    seen[task.name] = False
    return False


def _cmd_exec(args: argparse.Namespace) -> int:
    config = load_config()

    if args.executor is not None and args.executor != "local":
        print(f"Error: unknown executor {args.executor!r}. Only 'local' is supported.")
        return 1

    with Context() as ctx:
        try:
            _load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = _match_targets(ctx.tasks, args.pattern, config.default)

        if args.dry_run:
            all_tasks = _collect_transitive(targets)
            db_path = Path(".cook.db")
            if db_path.exists():
                with SqliteBuildStore(str(db_path)) as store:
                    seen: dict[str, bool] = {}
                    for task in all_tasks:
                        stale = _compute_staleness(task, store, seen)
                        status = "STALE (would run)" if stale else "up-to-date"
                        print(f"[{task.name}] {status}")
            else:
                for task in all_tasks:
                    print(f"[{task.name}] STALE (would run)")
            return 0

        executor = LocalExecutor(max_concurrent=config.local_max_concurrent)
        with SqliteBuildStore(".cook.db") as store:
            scheduler = Scheduler(store, executor, keep_going=args.keep_going)
            asyncio.run(scheduler.run(targets))

    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    config = load_config()

    with Context() as ctx:
        try:
            _load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = _match_targets(ctx.tasks, args.pattern, config.default)

        all_tasks = _collect_transitive(targets)
        db_path = Path(".cook.db")
        if db_path.exists():
            with SqliteBuildStore(str(db_path)) as store:
                seen: dict[str, bool] = {}
                for task in all_tasks:
                    stale = _compute_staleness(task, store, seen)
                    status = "STALE" if stale else "up-to-date"
                    deps = ", ".join(d.name for d in task.task_deps)
                    dep_str = f" (deps: {deps})" if deps else ""
                    print(f"[{task.name}] {status}{dep_str}")
        else:
            for task in all_tasks:
                deps = ", ".join(d.name for d in task.task_deps)
                dep_str = f" (deps: {deps})" if deps else ""
                print(f"[{task.name}] STALE{dep_str}")

    return 0


def _cmd_invalidate(args: argparse.Namespace) -> int:
    config = load_config()

    with Context() as ctx:
        try:
            _load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = _match_targets(ctx.tasks, args.pattern, config.default)

        with SqliteBuildStore(".cook.db") as store:
            for task in targets:
                store.delete(task.task_id)
                print(f"Invalidated [{task.name}]")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handlers = {
        "exec": _cmd_exec,
        "inspect": _cmd_inspect,
        "invalidate": _cmd_invalidate,
    }

    try:
        return handlers[args.command](args)
    except (
        ValueError,
        ConfigError,
        BuildError,
        TaskExecutionError,
        TaskOutputError,
        FileNotFoundError,
    ) as e:
        print(f"Error: {e}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
