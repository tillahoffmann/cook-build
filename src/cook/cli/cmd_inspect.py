from __future__ import annotations

import argparse
import json

from ..config import Config
from ..context import Context
from ..scheduler import is_stale, staleness_reason
from ..store import FileDigestCache, TaskRecord
from ..store.sqlite import SqliteBuildStore
from ..task import ShellTask, Task
from ..transform import collect_transitive
from ..ui import Output
from .util import match_targets, print_task_detail


def _task_to_dict(
    task: Task, stale: bool, record: TaskRecord | None, reason: str | None = None
) -> dict[str, object]:
    obj: dict[str, object] = {
        "name": task.name,
        "type": type(task).__name__,
        "stale": stale,
        "reason": reason,
        "deps": [d.name for d in task.task_deps],
        "inputs": [str(f) for f in task.file_inputs],
        "outputs": [str(o) for o in task.outputs],
    }
    if isinstance(task, ShellTask) and task.cmd:
        obj["cmd"] = task.cmd
    if record is not None:
        history: dict[str, object] = {}
        if record.last_started:
            history["last_started"] = record.last_started.isoformat()
        if record.last_succeeded:
            history["last_succeeded"] = record.last_succeeded.isoformat()
        if record.last_failed:
            history["last_failed"] = record.last_failed.isoformat()
        if record.error:
            history["error"] = record.error
        if record.duration is not None:
            history["duration"] = round(record.duration, 3)
        if history:
            obj["history"] = history
    return obj


def cmd_inspect(
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)
    use_json = args.json

    all_tasks = collect_transitive(targets)
    db_path = ctx.db_path
    if db_path.exists():
        cache = FileDigestCache()
        with SqliteBuildStore(str(db_path)) as store:
            for task in all_tasks:
                stale = is_stale(task, store, cache, project_root=ctx.project_root)
                reason = staleness_reason(task, store, cache, ctx.project_root)
                record = store.get(task.task_id)
                if use_json:
                    print(json.dumps(_task_to_dict(task, stale, record, reason)))
                else:
                    print_task_detail(task, stale, record, ui, reason)
    else:
        for task in all_tasks:
            reason = "never run" if task.outputs else "always-run (no outputs)"
            if use_json:
                print(json.dumps(_task_to_dict(task, True, None, reason)))
            else:
                print_task_detail(task, True, None, ui, reason)

    return 0
