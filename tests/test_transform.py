from __future__ import annotations

from pathlib import Path

import pytest

from cook.task import ShellTask, Task
from cook.transform import (
    check_cycles,
    check_deps_registered,
    check_extra_keys,
    check_outputs,
    resolve_file_deps,
)


def _tasks(*task_list: Task) -> dict[str, Task]:
    return {t.name: t for t in task_list}


# --- check_deps_registered ---


def test_check_deps_registered_valid() -> None:
    a = Task(name="a")
    b = Task(name="b", inputs=[a])
    check_deps_registered(_tasks(a, b))


def test_check_deps_registered_missing() -> None:
    ghost = Task(name="ghost")
    a = Task(name="a", inputs=[ghost])
    with pytest.raises(ValueError, match="not registered"):
        check_deps_registered(_tasks(a))


# --- check_outputs ---


def test_check_outputs_valid() -> None:
    a = Task(name="a", outputs=["a.o"])
    b = Task(name="b", outputs=["b.o"])
    check_outputs(_tasks(a, b))


def test_check_outputs_duplicate() -> None:
    a = Task(name="a", outputs=["out.o"])
    b = Task(name="b", outputs=["out.o"])
    with pytest.raises(ValueError, match="Duplicate output path"):
        check_outputs(_tasks(a, b))


def test_check_outputs_duplicate_resolved(tmp_path: Path) -> None:
    p = str(tmp_path / "out.o")
    a = Task(name="a", outputs=[p])
    b = Task(name="b", outputs=[p])
    with pytest.raises(ValueError, match="Duplicate output path"):
        check_outputs(_tasks(a, b))


def test_check_outputs_input_overlaps_output() -> None:
    a = Task(name="a", inputs=["file.txt"], outputs=["file.txt"])
    with pytest.raises(ValueError, match="both an input and an output"):
        check_outputs(_tasks(a))


# --- resolve_file_deps ---


def test_resolve_adds_producer() -> None:
    a = ShellTask(name="compile", cmd="cc -c", outputs=["foo.o"])
    b = ShellTask(name="link", cmd="cc", inputs=["foo.o"], outputs=["app"])
    resolve_file_deps(_tasks(a, b))
    assert a in b.task_deps


def test_resolve_no_match() -> None:
    a = ShellTask(name="compile", cmd="cc -c", outputs=["foo.o"])
    b = ShellTask(name="link", cmd="cc", inputs=["bar.o"], outputs=["app"])
    resolve_file_deps(_tasks(a, b))
    assert a not in b.task_deps


def test_resolve_already_explicit_dep() -> None:
    a = ShellTask(name="compile", cmd="cc -c", outputs=["foo.o"])
    b = ShellTask(name="link", cmd="cc", inputs=[a, "foo.o"], outputs=["app"])
    resolve_file_deps(_tasks(a, b))
    # Should not add a duplicate
    assert b.task_deps == [a]


def test_resolve_preserves_file_input() -> None:
    a = ShellTask(name="compile", cmd="cc -c", outputs=["foo.o"])
    b = ShellTask(name="link", cmd="cc", inputs=["foo.o"], outputs=["app"])
    resolve_file_deps(_tasks(a, b))
    assert "foo.o" in b.file_inputs
    assert a in b.task_deps


def test_resolve_chain() -> None:
    a = ShellTask(name="a", cmd="a", outputs=["x.o"])
    b = ShellTask(name="b", cmd="b", inputs=["x.o"], outputs=["y.o"])
    c = ShellTask(name="c", cmd="c", inputs=["y.o"], outputs=["z.o"])
    resolve_file_deps(_tasks(a, b, c))
    assert a in b.task_deps
    assert b in c.task_deps


def test_resolve_diamond() -> None:
    a = ShellTask(name="a", cmd="a", outputs=["shared.o"])
    b = ShellTask(name="b", cmd="b", inputs=["shared.o"], outputs=["b.o"])
    c = ShellTask(name="c", cmd="c", inputs=["shared.o"], outputs=["c.o"])
    resolve_file_deps(_tasks(a, b, c))
    assert a in b.task_deps
    assert a in c.task_deps


# --- check_cycles ---


def test_check_cycles_valid() -> None:
    a = Task(name="a")
    b = Task(name="b", inputs=[a])
    check_cycles(_tasks(a, b))


def test_check_cycles_direct() -> None:
    a = Task(name="a")
    b = Task(name="b")
    a.inputs = [b]
    b.inputs = [a]
    with pytest.raises(ValueError, match="cycle"):
        check_cycles(_tasks(a, b))


def test_check_cycles_self() -> None:
    a = Task(name="a")
    a.inputs = [a]
    with pytest.raises(ValueError, match="cycle"):
        check_cycles(_tasks(a))


def test_check_cycles_diamond_no_false_positive() -> None:
    d = Task(name="d")
    b = Task(name="b", inputs=[d])
    c = Task(name="c", inputs=[d])
    a = Task(name="a", inputs=[b, c])
    check_cycles(_tasks(a, b, c, d))


# --- integration: resolve + cycle detection ---


def test_resolve_preserves_existing_deps() -> None:
    """Pre-populated deps are not overwritten by resolve_file_deps."""
    a = ShellTask(name="a", cmd="a")
    b = ShellTask(name="b", cmd="b", outputs=["x.o"])
    c = ShellTask(name="c", cmd="c", inputs=["x.o"])
    c._deps.add(a)
    resolve_file_deps(_tasks(a, b, c))
    assert a in c.task_deps
    assert b in c.task_deps


def test_resolve_idempotent() -> None:
    """Running resolve_file_deps twice gives the same result."""
    a = ShellTask(name="a", cmd="a", outputs=["x.o"])
    b = ShellTask(name="b", cmd="b", inputs=["x.o"])
    tasks = _tasks(a, b)
    resolve_file_deps(tasks)
    assert a in b.task_deps
    resolve_file_deps(tasks)
    assert a in b.task_deps
    assert b.task_deps == [a]


def test_resolve_creates_cycle() -> None:
    """A outputs x.o, B inputs x.o and A depends on B → cycle after resolution."""
    a = ShellTask(name="a", cmd="a", inputs=[], outputs=["x.o"])
    b = ShellTask(name="b", cmd="b", inputs=["x.o"])
    a.inputs = [b]  # explicit dep: a -> b
    # After resolution: b -> a (via x.o), creating a -> b -> a cycle
    resolve_file_deps(_tasks(a, b))
    with pytest.raises(ValueError, match="cycle"):
        check_cycles(_tasks(a, b))


# --- check_extra_keys ---


def test_extra_keys_valid_executor_name() -> None:
    a = ShellTask(name="a", cmd="echo", extra={"slurm": {"mem": "8G"}})
    check_extra_keys(_tasks(a))  # should not raise


def test_extra_keys_unknown_rejected() -> None:
    a = ShellTask(name="a", cmd="echo", extra={"bogus": {"x": "y"}})
    with pytest.raises(ValueError, match="unknown extra key.*bogus"):
        check_extra_keys(_tasks(a))


def test_extra_keys_empty_ok() -> None:
    a = ShellTask(name="a", cmd="echo")
    check_extra_keys(_tasks(a))  # should not raise
