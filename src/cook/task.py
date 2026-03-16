from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

_GLOB_CHARS = set("*?[]")


def _validate_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("Task name must not be empty or whitespace-only.")
    if any(c in _GLOB_CHARS for c in name):
        raise ValueError(
            f"Task name {name!r} contains glob characters (*?[]). "
            "Task names must not contain glob characters."
        )


@dataclass(eq=False)
class Task:
    name: str
    inputs: list[str | Path | Task] = field(default_factory=list)
    outputs: list[str | Path] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)
    _deps: set[Task] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        _validate_name(self.name)

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other

    @property
    def task_id(self) -> str:
        return self.name

    @property
    def task_deps(self) -> list[Task]:
        explicit = [i for i in self.inputs if isinstance(i, Task)]
        seen = {t.name for t in explicit}
        merged = list(explicit)
        for d in sorted(self._deps, key=lambda t: t.name):
            if d.name not in seen:
                merged.append(d)
                seen.add(d.name)
        return merged

    @property
    def file_inputs(self) -> list[str | Path]:
        return [i for i in self.inputs if not isinstance(i, Task)]

    def digest(self) -> str:
        """Return SHA-256 hex digest of this task's own identity.

        Uses JSON serialization for deterministic output. Path objects
        are normalized to strings so str("foo") and Path("foo") produce
        the same digest. Task objects in inputs are filtered out.
        """
        h = hashlib.sha256()
        h.update(type(self).__name__.encode())
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "inputs":
                value = [str(v) for v in value if not isinstance(v, Task)]
            elif f.name == "_deps":
                continue
            elif isinstance(value, list):
                value = [str(v) for v in value]
            h.update(f"{f.name}:{json.dumps(value, sort_keys=True)}".encode())
        return h.hexdigest()


@dataclass(eq=False)
class ShellTask(Task):
    cmd: str = ""
    env: dict[str, str] | None = None
    cwd: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.cmd or not self.cmd.strip():
            raise ValueError(
                f"ShellTask {self.name!r}: cmd must not be empty or whitespace-only."
            )
