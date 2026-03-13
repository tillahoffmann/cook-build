from __future__ import annotations

import argparse
from pathlib import Path

from ..config import load_config
from ..context import Context
from ..scheduler import compute_effective_digest
from ..store import TaskRecord
from ..store.sqlite import SqliteBuildStore
from .util import collect_transitive, load_recipe, match_targets


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config()

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        ctx.validate()
        targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

        all_tasks = collect_transitive(targets)
        with SqliteBuildStore(".cook.db") as store:
            for task in all_tasks:
                effective = compute_effective_digest(task, store)
                if effective is None:
                    print(
                        f"[{task.name}] skipped (no outputs or always-run dependency)"
                    )
                    continue
                missing = [
                    Path(o).resolve()
                    for o in task.outputs
                    if not Path(o).resolve().exists()
                ]
                if missing:
                    paths = ", ".join(str(p) for p in missing)
                    print(f"[{task.name}] skipped (missing outputs: {paths})")
                    continue
                store.save(TaskRecord(task_id=task.task_id, digest=effective))
                print(f"[{task.name}] validated")

    return 0
