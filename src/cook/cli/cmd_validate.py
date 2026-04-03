from __future__ import annotations

import argparse

from ..config import Config
from ..context import Context
from ..scheduler import compute_effective_digest
from ..store import TaskRecord
from ..store.sqlite import SqliteBuildStore
from ..transform import collect_transitive
from ..ui import Output
from .util import match_targets


def cmd_validate(
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)

    all_tasks = collect_transitive(targets)

    # Check if any tasks can actually be validated before creating the db
    validatable = []
    for task in all_tasks:
        if not task.outputs:
            ui.status(f"[{task.name}] skipped (no outputs or always-run dependency)")
            continue
        missing = [
            ctx.resolve_to_resource(o).label
            for o in task.outputs
            if not ctx.resolve_to_resource(o).exists()
        ]
        if missing:
            paths = ", ".join(missing)
            ui.status(f"[{task.name}] skipped (missing outputs: {paths})")
            continue
        validatable.append(task)

    if not validatable:
        return 0

    with SqliteBuildStore(str(ctx.db_path)) as store:
        for task in validatable:
            effective = compute_effective_digest(
                task, store, project_root=ctx.project_root
            )
            if effective is None:
                ui.status(f"[{task.name}] skipped (always-run dependency)")
                continue
            store.save(TaskRecord(task_id=task.task_id, digest=effective))
            ui.status(f"[{task.name}] validated")

    return 0
