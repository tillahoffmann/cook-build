from pathlib import Path

import pytest

from cook import ShellTask, Task


def test_task_creation() -> None:
    t = Task(name="build", inputs=["a.c", Path("b.c")], outputs=["a.o"])
    assert t.name == "build"
    assert t.inputs == ["a.c", Path("b.c")]
    assert t.outputs == ["a.o"]


def test_empty_name_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        Task(name="")


def test_whitespace_name_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        Task(name="   ")


def test_task_defaults() -> None:
    t = Task(name="empty")
    assert t.inputs == []
    assert t.outputs == []


def test_task_id_returns_name() -> None:
    t = Task(name="foo")
    assert t.task_id == "foo"


def test_task_deps_filters_tasks() -> None:
    dep = Task(name="dep")
    t = Task(name="main", inputs=[dep, "file.c"])
    assert t.task_deps == [dep]


def test_file_inputs_filters_non_tasks() -> None:
    dep = Task(name="dep")
    t = Task(name="main", inputs=[dep, "file.c", Path("other.c")])
    assert t.file_inputs == ["file.c", Path("other.c")]


def test_digest_consistent() -> None:
    t1 = Task(name="a", inputs=["x"], outputs=["y"])
    t2 = Task(name="a", inputs=["x"], outputs=["y"])
    assert t1.digest() == t2.digest()


def test_digest_is_hex_string() -> None:
    t = Task(name="a")
    d = t.digest()
    assert isinstance(d, str)
    assert len(d) == 64
    int(d, 16)  # valid hex


def test_digest_changes_with_name() -> None:
    t1 = Task(name="a")
    t2 = Task(name="b")
    assert t1.digest() != t2.digest()


def test_digest_changes_with_inputs() -> None:
    t1 = Task(name="a", inputs=["x"])
    t2 = Task(name="a", inputs=["y"])
    assert t1.digest() != t2.digest()


def test_digest_changes_with_outputs() -> None:
    t1 = Task(name="a", outputs=["x"])
    t2 = Task(name="a", outputs=["y"])
    assert t1.digest() != t2.digest()


def test_digest_filters_out_task_deps() -> None:
    dep1 = Task(name="dep1")
    dep2 = Task(name="dep2")
    t1 = Task(name="a", inputs=[dep1, "file.c"])
    t2 = Task(name="a", inputs=[dep2, "file.c"])
    assert t1.digest() == t2.digest()


def test_digest_affected_by_input_order() -> None:
    """Input order is part of identity (supports future $^-like semantics)."""
    t1 = Task(name="a", inputs=["x", "y"])
    t2 = Task(name="a", inputs=["y", "x"])
    assert t1.digest() != t2.digest()


def test_digest_str_vs_path_equivalent() -> None:
    """str and Path inputs should produce the same digest."""
    t1 = Task(name="a", inputs=["foo.c"], outputs=["foo.o"])
    t2 = Task(name="a", inputs=[Path("foo.c")], outputs=[Path("foo.o")])
    assert t1.digest() == t2.digest()


def test_deps_included_in_task_deps() -> None:
    a = Task(name="a")
    b = Task(name="b")
    b._deps.add(a)
    assert a in b.task_deps


def test_deps_merged_with_explicit_inputs() -> None:
    a = Task(name="a")
    b = Task(name="b")
    c = Task(name="c", inputs=[a])
    c._deps.add(b)
    assert a in c.task_deps
    assert b in c.task_deps


def test_deps_deduped_with_inputs() -> None:
    a = Task(name="a")
    b = Task(name="b", inputs=[a])
    b._deps.add(a)
    assert b.task_deps == [a]


def test_digest_ignores_deps() -> None:
    a = Task(name="other")
    t1 = Task(name="x")
    t2 = Task(name="x")
    t2._deps.add(a)
    assert t1.digest() == t2.digest()


def test_deps_default_empty() -> None:
    t = Task(name="a")
    assert t._deps == set()


def test_shelltask_inherits_task() -> None:
    t = ShellTask(name="sh", cmd="true")
    assert isinstance(t, Task)


def test_shelltask_defaults() -> None:
    t = ShellTask(name="sh", cmd="true")
    assert t.env is None
    assert t.cwd is None


def test_shelltask_empty_cmd_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ShellTask(name="sh")


def test_shelltask_whitespace_cmd_rejected() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        ShellTask(name="sh", cmd="   ")


def test_shelltask_fields() -> None:
    t = ShellTask(
        name="build",
        cmd="gcc -o out main.c",
        env={"CC": "gcc"},
        cwd="/tmp",
        inputs=["main.c"],
        outputs=["out"],
    )
    assert t.cmd == "gcc -o out main.c"
    assert t.env == {"CC": "gcc"}
    assert t.cwd == "/tmp"


def test_shelltask_digest_changes_with_cmd() -> None:
    t1 = ShellTask(name="a", cmd="echo 1")
    t2 = ShellTask(name="a", cmd="echo 2")
    assert t1.digest() != t2.digest()


def test_shelltask_digest_changes_with_env() -> None:
    t1 = ShellTask(name="a", cmd="true", env={"A": "1"})
    t2 = ShellTask(name="a", cmd="true", env={"A": "2"})
    assert t1.digest() != t2.digest()


def test_shelltask_digest_changes_with_cwd() -> None:
    t1 = ShellTask(name="a", cmd="true", cwd="/a")
    t2 = ShellTask(name="a", cmd="true", cwd="/b")
    assert t1.digest() != t2.digest()


def test_shelltask_digest_sorts_env_keys() -> None:
    t1 = ShellTask(name="a", cmd="true", env={"B": "2", "A": "1"})
    t2 = ShellTask(name="a", cmd="true", env={"A": "1", "B": "2"})
    assert t1.digest() == t2.digest()


def test_name_validation_rejects_star() -> None:
    with pytest.raises(ValueError, match="glob"):
        Task(name="build*")


def test_name_validation_rejects_question() -> None:
    with pytest.raises(ValueError, match="glob"):
        Task(name="build?")


def test_name_validation_rejects_bracket_open() -> None:
    with pytest.raises(ValueError, match="glob"):
        Task(name="build[1]")


def test_name_validation_rejects_bracket_close() -> None:
    with pytest.raises(ValueError, match="glob"):
        Task(name="build]")


def test_name_validation_accepts_normal_names() -> None:
    Task(name="compile-foo")
    Task(name="build/app")
    Task(name="test_unit")
    Task(name="a.b.c")


def test_shelltask_name_validation() -> None:
    with pytest.raises(ValueError, match="glob"):
        ShellTask(name="bad*name", cmd="echo hi")


def test_task_identity_equality() -> None:
    t = Task(name="a")
    assert t == t
    assert hash(t) == hash(t)


def test_task_distinct_objects_not_equal() -> None:
    t1 = Task(name="a")
    t2 = Task(name="a")
    assert t1 != t2


def test_task_usable_in_set() -> None:
    t1 = Task(name="a")
    t2 = Task(name="b")
    s = {t1, t2, t1}
    assert len(s) == 2


def test_shelltask_digest_includes_class_name() -> None:
    """Task and ShellTask with same name should have different digests
    because the class name is part of the hash."""
    t = Task(name="a")
    st = ShellTask(name="a", cmd="true")
    assert t.digest() != st.digest()


def test_extra_defaults_empty() -> None:
    t = Task(name="a")
    assert t.extra == {}


def test_extra_stored() -> None:
    t = ShellTask(
        name="a",
        cmd="echo hi",
        extra={"slurm": {"mem": "4G", "time": "01:00:00"}},
    )
    assert t.extra["slurm"]["mem"] == "4G"
    assert t.extra["slurm"]["time"] == "01:00:00"


def test_extra_affects_digest() -> None:
    t1 = ShellTask(name="a", cmd="echo hi")
    t2 = ShellTask(name="a", cmd="echo hi", extra={"slurm": {"mem": "4G"}})
    assert t1.digest() != t2.digest()


def test_shelltask_cmd_sequence() -> None:
    t = ShellTask(name="a", cmd=["gcc", "-o", "main", "main.c"])
    assert t.cmd == ["gcc", "-o", "main", "main.c"]


def test_shelltask_cmd_sequence_empty_rejected() -> None:
    with pytest.raises(ValueError, match="cmd must not be empty"):
        ShellTask(name="a", cmd=[])


def test_shelltask_cmd_sequence_digest_differs_from_str() -> None:
    t1 = ShellTask(name="a", cmd="echo hello")
    t2 = ShellTask(name="a", cmd=["echo", "hello"])
    assert t1.digest() != t2.digest()
