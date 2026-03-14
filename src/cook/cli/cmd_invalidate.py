from __future__ import annotations

import argparse

from ..config import Config
from ..context import Context
from ..store.sqlite import SqliteBuildStore
from .util import match_targets


def cmd_invalidate(args: argparse.Namespace, config: Config, ctx: Context) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

    with SqliteBuildStore(".cook.db") as store:
        for task in targets:
            store.delete(task.task_id)
            print(f"Invalidated [{task.name}]")

    return 0
