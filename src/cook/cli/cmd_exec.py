from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..config import Config
from ..context import Context
from ..scheduler import Scheduler, is_stale
from ..store import FileDigestCache
from ..store.sqlite import SqliteBuildStore
from ..ui import Output
from .util import collect_transitive, match_targets


def cmd_exec(args: argparse.Namespace, config: Config, ctx: Context, ui: Output) -> int:

    executor_name = args.executor if args.executor is not None else config.executor

    from ..executor import get_executor

    try:
        executor_cls = get_executor(executor_name)
    except ValueError as e:
        ui.error(str(e))
        return 1

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
