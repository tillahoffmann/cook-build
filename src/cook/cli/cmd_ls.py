from __future__ import annotations

import argparse
import json

from ..config import Config
from ..context import Context
from ..scheduler import StalenessChecker
from ..store import FileDigestCache
from ..store.sqlite import SqliteBuildStore
from ..ui import Output
from .util import match_targets


def _print_task(name: str, use_json: bool, stale: bool | None = None) -> None:
    if use_json:
        obj: dict[str, object] = {"name": name}
        if stale is not None:
            obj["stale"] = stale
        print(json.dumps(obj))
    else:
        print(name)


def cmd_ls(args: argparse.Namespace, config: Config, ctx: Context, ui: Output) -> int:
    if args.pattern:
        tasks = match_targets(ctx.tasks, args.pattern, config.default, args.regex)
    else:
        tasks = list(ctx.tasks.values())

    use_json = args.json

    if args.stale or args.current:
        db_path = ctx.db_path
        if db_path.exists():
            with SqliteBuildStore(str(db_path)) as store:
                checker = StalenessChecker(
                    store, FileDigestCache(), project_root=ctx.project_root
                )
                for task in tasks:
                    stale = checker.is_stale(task)
                    if (args.stale and stale) or (args.current and not stale):
                        _print_task(task.name, use_json, stale)
        else:
            # No store means everything is stale
            if args.stale:
                for task in tasks:
                    _print_task(task.name, use_json, True)
            # --current with no store: nothing is current
    else:
        for task in tasks:
            _print_task(task.name, use_json)

    return 0
