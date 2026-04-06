from collections.abc import Generator
from pathlib import Path

import pytest

from cook import Context, ShellTask, Task, get_context


@pytest.fixture
def ctx() -> Generator[Context]:
    with Context() as c:
        yield c


def test_register_stores_and_returns_task(ctx: Context) -> None:
    t = Task(name="a")
    result = ctx.register(t)
    assert result is t
    assert "a" in ctx.tasks


def test_register_raises_on_duplicate(ctx: Context) -> None:
    ctx.register(Task(name="a"))
    with pytest.raises(ValueError, match="duplicate name"):
        ctx.register(Task(name="a"))


def test_register_duplicate_shows_both_locations(ctx: Context) -> None:
    ctx.register(Task(name="dup"))
    with pytest.raises(ValueError, match="test_context.py") as exc_info:
        ctx.register(Task(name="dup"))
    msg = str(exc_info.value)
    # Both the original and new registration locations should appear
    assert msg.count("test_context.py") == 2


def test_sh_creates_and_registers(ctx: Context) -> None:
    t = ctx.sh(name="build", cmd="make")
    assert isinstance(t, ShellTask)
    assert t.name == "build"
    assert t.cmd == "make"
    assert "build" in ctx.tasks


def test_sh_returns_task_for_chaining(ctx: Context) -> None:
    t = ctx.sh(name="a", cmd="echo a")
    t2 = ctx.sh(name="b", cmd="echo b", inputs=[t])
    assert t in t2.task_deps


def test_sh_passes_all_params(ctx: Context) -> None:
    t = ctx.sh(
        name="x",
        cmd="gcc",
        inputs=["a.c"],
        outputs=["a.o"],
        env={"CC": "gcc"},
        cwd="/tmp",
    )
    assert t.inputs == ["a.c"]
    assert t.outputs == ["a.o"]
    assert t.env == {"CC": "gcc"}
    assert t.cwd == "/tmp"


def test_tasks_property_returns_copy(ctx: Context) -> None:
    ctx.register(Task(name="a"))
    tasks = ctx.tasks
    tasks["b"] = Task(name="b")
    assert "b" not in ctx.tasks


def test_context_manager_activates(ctx: Context) -> None:
    assert get_context() is ctx


def test_context_manager_deactivates() -> None:
    with Context() as inner:
        assert get_context() is inner
    assert get_context() is not inner


def test_get_context_returns_default_singleton() -> None:
    # Outside any context manager, get_context returns the default singleton
    import cook.context as mod

    old = mod._default_context
    mod._default_context = None
    try:
        c1 = get_context()
        c2 = get_context()
        assert c1 is c2
    finally:
        mod._default_context = old


def test_nested_contexts() -> None:
    with Context() as outer:
        assert get_context() is outer
        with Context() as inner:
            assert get_context() is inner
        assert get_context() is outer


def test_validate_passes_valid_graph(ctx: Context) -> None:
    a = ctx.sh(name="a", cmd="echo a", outputs=["a.o"])
    ctx.sh(name="b", cmd="echo b", inputs=[a, "a.o"], outputs=["b.o"])
    ctx.validate()


def test_validate_fails_unregistered_dep() -> None:
    with Context() as ctx:
        unregistered = Task(name="ghost")
        ctx.sh(name="a", cmd="echo a", inputs=[unregistered])
        with pytest.raises(ValueError, match="not registered"):
            ctx.validate()


def test_validate_fails_duplicate_outputs(ctx: Context) -> None:
    ctx.sh(name="a", cmd="echo a", outputs=["out.o"])
    ctx.sh(name="b", cmd="echo b", outputs=["out.o"])
    with pytest.raises(ValueError, match="duplicate output"):
        ctx.validate()


def test_validate_fails_file_input_overlaps_output(ctx: Context) -> None:
    ctx.sh(name="a", cmd="echo a", inputs=["file.txt"], outputs=["file.txt"])
    with pytest.raises(ValueError, match="both an input and an output"):
        ctx.validate()


def test_validate_fails_cycle(ctx: Context) -> None:
    a = Task(name="a")
    b = Task(name="b")
    a.inputs = [b]
    b.inputs = [a]
    ctx.register(a)
    ctx.register(b)
    with pytest.raises(ValueError, match="cycle"):
        ctx.validate()


def test_validate_fails_self_cycle(ctx: Context) -> None:
    a = Task(name="a")
    a.inputs = [a]
    ctx.register(a)
    with pytest.raises(ValueError, match="cycle"):
        ctx.validate()


def test_validate_passes_diamond(ctx: Context) -> None:
    d = ctx.sh(name="d", cmd="echo d", outputs=["d.o"])
    b = ctx.sh(name="b", cmd="echo b", inputs=[d], outputs=["b.o"])
    c = ctx.sh(name="c", cmd="echo c", inputs=[d], outputs=["c.o"])
    ctx.sh(name="a", cmd="echo a", inputs=[b, c], outputs=["a.o"])
    ctx.validate()


def test_validate_duplicate_outputs_resolved_paths(
    ctx: Context, tmp_path: Path
) -> None:
    # Same file via different relative/absolute path representations
    abs_path = str(tmp_path / "out.o")
    ctx.sh(name="a", cmd="echo a", outputs=[abs_path])
    ctx.sh(name="b", cmd="echo b", outputs=[abs_path])
    with pytest.raises(ValueError, match="duplicate output"):
        ctx.validate()


def test_validate_resolves_file_deps(ctx: Context) -> None:
    """File input matching another task's output creates an implicit dep."""
    compile_task = ctx.sh(name="compile", cmd="cc -c", outputs=["foo.o"])
    link_task = ctx.sh(name="link", cmd="cc", inputs=["foo.o"], outputs=["app"])
    ctx.validate()
    assert compile_task in link_task.task_deps
    # Original inputs list is not modified
    assert compile_task not in link_task.inputs


def test_validate_idempotent(ctx: Context) -> None:
    """Calling validate() twice produces the same result."""
    compile_task = ctx.sh(name="compile", cmd="cc -c", outputs=["foo.o"])
    link_task = ctx.sh(name="link", cmd="cc", inputs=["foo.o"], outputs=["app"])
    ctx.validate()
    tasks_after_first = dict(ctx.tasks)
    deps_after_first = list(link_task.task_deps)

    ctx.validate()
    assert ctx.tasks == tasks_after_first
    assert list(link_task.task_deps) == deps_after_first
    assert compile_task in link_task.task_deps


def test_sh_extra_kwargs() -> None:
    with Context() as ctx:
        task = ctx.sh(
            name="gpu",
            cmd="train.py",
            slurm={"mem": "8G", "partition": "gpu"},
        )
    assert task.extra == {"slurm": {"mem": "8G", "partition": "gpu"}}


def test_top_level_sh() -> None:
    from cook import sh

    with Context() as ctx:
        task = sh(name="top-level", cmd="echo hello", outputs=["out.txt"])
    assert "top-level" in ctx.tasks
    assert task.cmd == "echo hello"


def test_project_root_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with Context() as ctx:
        assert ctx.project_root == tmp_path


def test_project_root_explicit(tmp_path: Path) -> None:
    root = tmp_path / "myproject"
    root.mkdir()
    with Context(project_root=root) as ctx:
        assert ctx.project_root == root


def test_resolve_relative(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        resolved = ctx.resolve("src/foo.c")
    assert resolved == (tmp_path / "src" / "foo.c").resolve()


def test_relative_inside_project(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        assert ctx.relative(tmp_path / "src" / "foo.c") == "src/foo.c"


def test_relative_outside_project(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        outside = Path("/usr/include/stdio.h")
        assert ctx.relative(outside) == str(outside)


def test_relative_already_relative(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        assert ctx.relative("foo.c") == "foo.c"


def test_relative_gcs_returns_label(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        assert ctx.relative("gs://bucket/key.txt") == "gs://bucket/key.txt"


def test_resolve_absolute(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        resolved = ctx.resolve("/usr/include/stdio.h")
    assert resolved == Path("/usr/include/stdio.h").resolve()


def test_create_task_raises() -> None:
    from cook import create_task

    with pytest.raises(NotImplementedError, match="cook.sh"):
        create_task(name="old-style", action="echo hello")


def test_group_creates_group_task(tmp_path: Path) -> None:
    from cook.task import GroupTask

    with Context(project_root=tmp_path) as ctx:
        with ctx.group("my-group") as g:
            ctx.sh(name="a", cmd="true", outputs=["a.txt"])
            ctx.sh(name="b", cmd="true", outputs=["b.txt"])

    assert isinstance(g, GroupTask)
    assert "my-group" in ctx.tasks
    deps = [d.name for d in g.task_deps]
    assert "a" in deps
    assert "b" in deps
    assert g.outputs == [tmp_path / ".cook" / "groups" / "my-group"]


def test_group_nested(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        with ctx.group("outer") as outer:
            ctx.sh(name="a", cmd="true", outputs=["a.txt"])
            with ctx.group("inner") as inner:
                ctx.sh(name="b", cmd="true", outputs=["b.txt"])

    # inner group is a dep of outer
    outer_deps = [d.name for d in outer.task_deps]
    assert "a" in outer_deps
    assert "inner" in outer_deps

    # b is a dep of inner, not directly of outer
    inner_deps = [d.name for d in inner.task_deps]
    assert "b" in inner_deps
    assert "b" not in outer_deps


def test_group_top_level_shortcut(tmp_path: Path) -> None:
    from cook import group

    with Context(project_root=tmp_path) as ctx:
        with group("top-level") as g:
            ctx.sh(name="t", cmd="true", outputs=["t.txt"])

    assert "top-level" in ctx.tasks
    assert "t" in [d.name for d in g.task_deps]


def test_group_as_dependency(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        with ctx.group("data") as data:
            ctx.sh(name="gen", cmd="true", outputs=["data.csv"])
        ctx.sh(name="train", cmd="true", inputs=[data], outputs=["model.pt"])

    train = ctx.tasks["train"]
    assert data in [d for d in train.task_deps]


def test_sh_cmd_sequence(ctx: Context) -> None:
    t = ctx.sh(name="build", cmd=["gcc", "-o", "main", "main.c"])
    assert t.cmd == ["gcc", "-o", "main", "main.c"]


def test_sh_cmd_sequence_empty_raises(ctx: Context) -> None:
    with pytest.raises(ValueError, match="cmd must not be empty"):
        ctx.sh(name="bad", cmd=[])


def test_sh_cmd_sequence_coerces_non_str(ctx: Context) -> None:
    t = ctx.sh(name="build", cmd=["gcc", Path("main.c"), 42])
    assert t.cmd == ["gcc", "main.c", "42"]


def test_db_path(tmp_path: Path) -> None:
    with Context(project_root=tmp_path) as ctx:
        assert ctx.db_path == tmp_path / ".cook" / "store.db"


def test_register_captures_source_location(ctx: Context) -> None:
    t = Task(name="loc-test")
    ctx.register(t)
    assert t.source_file is not None
    assert t.source_line is not None
    assert "test_context.py" in t.source_file
    assert isinstance(t.source_line, int)


def test_sh_captures_source_location(ctx: Context) -> None:
    t = ctx.sh(name="loc-sh", cmd="echo hi")
    assert t.source_file is not None
    assert "test_context.py" in t.source_file


def test_group_captures_source_location(ctx: Context) -> None:
    with ctx.group("loc-group") as g:
        pass
    assert g.source_file is not None
    assert "test_context.py" in g.source_file
