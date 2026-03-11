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
    with pytest.raises(ValueError, match="Duplicate task name"):
        ctx.register(Task(name="a"))


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
    with pytest.raises(ValueError, match="Duplicate output path"):
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
    with pytest.raises(ValueError, match="Duplicate output path"):
        ctx.validate()


def test_validate_resolves_file_deps(ctx: Context) -> None:
    """File input matching another task's output creates an implicit dep."""
    compile_task = ctx.sh(name="compile", cmd="cc -c", outputs=["foo.o"])
    link_task = ctx.sh(name="link", cmd="cc", inputs=["foo.o"], outputs=["app"])
    ctx.validate()
    assert compile_task in link_task.task_deps
    # Original inputs list is not modified
    assert compile_task not in link_task.inputs
