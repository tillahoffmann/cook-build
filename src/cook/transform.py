from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .executor import registered_executor_names
from .task import Task


class GraphTransform(Protocol):
    def __call__(
        self, tasks: dict[str, Task], project_root: Path | None = None
    ) -> dict[str, Task]: ...


def _resolve(path: str | Path, root: Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    return (root / p).resolve()


def check_deps_registered(
    tasks: dict[str, Task], project_root: Path | None = None
) -> dict[str, Task]:
    for task in tasks.values():
        for dep in task.task_deps:
            if dep.name not in tasks:
                raise ValueError(
                    f"Task {task.name!r} depends on {dep.name!r}, "
                    "which is not registered in this context."
                )
    return tasks


def check_outputs(
    tasks: dict[str, Task], project_root: Path | None = None
) -> dict[str, Task]:
    root = project_root or Path.cwd()
    seen_outputs: dict[Path, str] = {}
    for task in tasks.values():
        for out in task.outputs:
            resolved = _resolve(out, root)
            if resolved in seen_outputs:
                raise ValueError(
                    f"Duplicate output path {str(out)!r}: "
                    f"both {seen_outputs[resolved]!r} and {task.name!r} "
                    "declare it as an output."
                )
            seen_outputs[resolved] = task.name

    for task in tasks.values():
        resolved_outputs = {_resolve(o, root) for o in task.outputs}
        for inp in task.file_inputs:
            resolved_inp = _resolve(inp, root)
            if resolved_inp in resolved_outputs:
                raise ValueError(
                    f"Task {task.name!r} has {str(inp)!r} as both "
                    "an input and an output."
                )
    return tasks


def resolve_file_deps(
    tasks: dict[str, Task], project_root: Path | None = None
) -> dict[str, Task]:
    """Add implicit task dependencies based on file input/output matching."""
    root = project_root or Path.cwd()
    output_index: dict[Path, Task] = {}
    for task in tasks.values():
        for out in task.outputs:
            output_index[_resolve(out, root)] = task

    for task in tasks.values():
        explicit = set(task.task_deps)
        for inp in task.file_inputs:
            resolved = _resolve(inp, root)
            producer = output_index.get(resolved)
            if producer is not None and producer not in explicit:
                task._deps.add(producer)
                explicit.add(producer)

    return tasks


def check_extra_keys(
    tasks: dict[str, Task], project_root: Path | None = None
) -> dict[str, Task]:
    """Check that all top-level extra keys match registered executor names."""
    valid = registered_executor_names()
    for task in tasks.values():
        unknown = set(task.extra) - valid
        if unknown:
            raise ValueError(
                f"Task {task.name!r}: unknown extra key(s): "
                f"{', '.join(sorted(unknown))}. "
                f"Valid keys are registered executor names: "
                f"{', '.join(sorted(valid))}"
            )
    return tasks


def check_cycles(
    tasks: dict[str, Task], project_root: Path | None = None
) -> dict[str, Task]:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {name: WHITE for name in tasks}

    def dfs(name: str, path: list[str]) -> None:
        color[name] = GRAY
        path.append(name)
        for dep in tasks[name].task_deps:
            if color[dep.name] == GRAY:
                cycle_start = path.index(dep.name)
                cycle = path[cycle_start:] + [dep.name]
                raise ValueError(f"Dependency cycle detected: {' -> '.join(cycle)}")
            if color[dep.name] == WHITE:
                dfs(dep.name, path)
        path.pop()
        color[name] = BLACK

    for name in tasks:
        if color[name] == WHITE:
            dfs(name, [])

    return tasks


def collect_transitive(tasks: list[Task]) -> list[Task]:
    """Collect all transitive dependencies in topological order (deps before dependents).

    This ordering is relied upon by cmd_validate which needs dep records
    stored before computing dependent digests.
    """
    visited: set[str] = set()
    result: list[Task] = []

    def walk(task: Task) -> None:
        if task.name in visited:
            return
        visited.add(task.name)
        for dep in task.task_deps:
            walk(dep)
        result.append(task)

    for t in tasks:
        walk(t)
    return result


DEFAULT_TRANSFORMS: list[GraphTransform] = [
    check_deps_registered,
    check_outputs,
    check_extra_keys,
    resolve_file_deps,
    check_cycles,
]
