from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..config import load_config
from ..context import Context
from ..executor import SlurmExecutor, get_executor
from ..scheduler import Scheduler, is_stale
from ..store import FileDigestCache
from ..store.sqlite import SqliteBuildStore
from .util import collect_transitive, load_recipe, match_targets


def cmd_exec(args: argparse.Namespace) -> int:
    config = load_config()
    if args.file is not None:
        config.recipe = args.file

    executor_name = args.executor if args.executor is not None else config.executor
    try:
        executor_cls = get_executor(executor_name)
    except ValueError as e:
        print(f"Error: {e}")
        return 1

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

        if args.dry_run:
            all_tasks = collect_transitive(targets)
            db_path = Path(".cook.db")
            if db_path.exists():
                cache = FileDigestCache()
                with SqliteBuildStore(str(db_path)) as store:
                    for task in all_tasks:
                        stale = is_stale(task, store, cache)
                        status = "STALE (would run)" if stale else "up-to-date"
                        print(f"[{task.name}] {status}")
            else:
                for task in all_tasks:
                    print(f"[{task.name}] STALE (would run)")
            return 0

        if executor_name == "slurm" and issubclass(executor_cls, SlurmExecutor):
            max_concurrent = (
                args.jobs if args.jobs is not None else config.slurm_max_concurrent
            )
            executor = executor_cls(
                max_concurrent=max_concurrent,
                poll_interval=config.slurm_poll_interval,
                poll_timeout=config.slurm_poll_timeout,
                poll_retries=config.slurm_poll_retries,
            )
        else:
            max_concurrent = (
                args.jobs if args.jobs is not None else config.local_max_concurrent
            )
            executor = executor_cls(max_concurrent=max_concurrent)
        with SqliteBuildStore(".cook.db") as store:
            scheduler = Scheduler(store, executor, keep_going=args.keep_going)
            asyncio.run(scheduler.run(targets))

    return 0
