from __future__ import annotations

import argparse
from pathlib import Path

from ..config import Config
from ..context import Context
from ..scheduler import compute_effective_digest
from ..store import TaskRecord
from ..store.sqlite import SqliteBuildStore
from ..ui import Output
from .util import collect_transitive, match_targets


def cmd_validate(
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

    all_tasks = collect_transitive(targets)
    with SqliteBuildStore(".cook.db") as store:
        for task in all_tasks:
            effective = compute_effective_digest(task, store)
            if effective is None:
                ui.status(
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
                ui.status(f"[{task.name}] skipped (missing outputs: {paths})")
                continue
            store.save(TaskRecord(task_id=task.task_id, digest=effective))
            ui.status(f"[{task.name}] validated")

    return 0
