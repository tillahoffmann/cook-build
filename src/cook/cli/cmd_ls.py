from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_config
from ..context import Context
from ..scheduler import is_stale
from ..store import FileDigestCache
from ..store.sqlite import SqliteBuildStore
from .util import load_recipe, match_targets


def cmd_ls(args: argparse.Namespace) -> int:
    config = load_config()

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()

        if args.pattern:
            tasks = match_targets(ctx.tasks, args.pattern, config.default, args.regex)
        else:
            tasks = list(ctx.tasks.values())

        if args.stale or args.current:
            db_path = Path(".cook.db")
            if db_path.exists():
                cache = FileDigestCache()
                with SqliteBuildStore(str(db_path)) as store:
                    for task in tasks:
                        stale = is_stale(task, store, cache)
                        if (args.stale and stale) or (args.current and not stale):
                            print(task.name)
            else:
                # No store means everything is stale
                if args.stale:
                    for task in tasks:
                        print(task.name)
                # --current with no store: nothing is current
        else:
            for task in tasks:
                print(task.name)

    return 0
