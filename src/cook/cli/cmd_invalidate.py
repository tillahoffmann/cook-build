from __future__ import annotations

import argparse
from pathlib import Path

from ..config import Config
from ..context import Context
from ..store.sqlite import SqliteBuildStore
from ..ui import Output
from .util import match_targets


def cmd_invalidate(
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

    db_path = Path(".cook.db")
    if not db_path.exists():
        ui.status("No build store found, nothing to invalidate.")
        return 0

    with SqliteBuildStore(str(db_path)) as store:
        for task in targets:
            store.delete(task.task_id)
            ui.status(f"Invalidated [{task.name}]")

    return 0
