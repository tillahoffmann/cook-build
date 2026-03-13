from __future__ import annotations

import argparse

from ..config import load_config
from ..context import Context
from ..store.sqlite import SqliteBuildStore
from .util import load_recipe, match_targets


def cmd_invalidate(args: argparse.Namespace) -> int:
    config = load_config()

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

        with SqliteBuildStore(".cook.db") as store:
            for task in targets:
                store.delete(task.task_id)
                print(f"Invalidated [{task.name}]")

    return 0
