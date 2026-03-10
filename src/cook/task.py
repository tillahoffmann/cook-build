from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, fields
from pathlib import Path

_GLOB_CHARS = set("*?[]")


def _validate_name(name: str) -> None:
    if any(c in _GLOB_CHARS for c in name):
        raise ValueError(
            f"Task name {name!r} contains glob characters (*?[]). "
            "Task names must not contain glob characters."
        )


@dataclass
class Task:
    name: str
    inputs: list[str | Path | Task] = field(default_factory=list)
    outputs: list[str | Path] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_name(self.name)

    @property
    def task_id(self) -> str:
        return self.name

    @property
    def task_deps(self) -> list[Task]:
        return [i for i in self.inputs if isinstance(i, Task)]

    @property
    def file_inputs(self) -> list[str | Path]:
        return [i for i in self.inputs if not isinstance(i, Task)]

    def digest(self) -> str:
        """Return SHA-256 hex digest of this task's own identity.

        Serialization strategy: for each dataclass field, we append
        "field_name:repr(value)" to the hash. Task objects in `inputs`
        are filtered out (the scheduler handles those via effective digest).
        Dict-typed fields are sorted by key before repr.
        """
        h = hashlib.sha256()
        h.update(type(self).__name__.encode())
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "inputs":
                # Filter out Task objects — only hash file inputs
                value = [v for v in value if not isinstance(v, Task)]
            if isinstance(value, dict):
                value = sorted(value.items())
            h.update(f"{f.name}:{value!r}".encode())
        return h.hexdigest()


@dataclass
class ShellTask(Task):
    cmd: str = ""
    env: dict[str, str] | None = None
    cwd: str | None = None
