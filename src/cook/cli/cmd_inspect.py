from __future__ import annotations

import argparse
from pathlib import Path

from ..config import Config
from ..context import Context
from ..scheduler import is_stale
from ..store import FileDigestCache
from ..store.sqlite import SqliteBuildStore
from .util import collect_transitive, load_recipe, match_targets, print_task_detail


def cmd_inspect(args: argparse.Namespace, config: Config) -> int:

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

        all_tasks = collect_transitive(targets)
        db_path = Path(".cook.db")
        if db_path.exists():
            cache = FileDigestCache()
            with SqliteBuildStore(str(db_path)) as store:
                for task in all_tasks:
                    stale = is_stale(task, store, cache)
                    record = store.get(task.task_id)
                    print_task_detail(task, stale, record)
        else:
            for task in all_tasks:
                print_task_detail(task, True, None)

    return 0
