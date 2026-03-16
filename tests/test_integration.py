"""End-to-end integration tests that invoke cook via subprocess."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

COOK_CMD = [sys.executable, "-m", "cook.cli"]


def _run(
    args: list[str], cwd: Path, *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        COOK_CMD + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
        timeout=30,
    )


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content))


# ---------------------------------------------------------------------------
# Scenario: C compilation pipeline
# ---------------------------------------------------------------------------


@pytest.fixture
def c_project(tmp_path: Path) -> Path:
    """Set up a C-like project with src/foo.c, src/bar.c and a recipe."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.c").write_text("int foo() { return 0; }\n")
    (src / "bar.c").write_text("int bar() { return 1; }\n")

    _write_file(
        tmp_path / "recipe.py",
        """\
        from pathlib import Path
        from cook import get_context

        ctx = get_context()

        sources = sorted(Path("src").glob("*.c"))
        objects = []
        for src in sources:
            obj_path = str(src.with_suffix(".o"))
            objects.append(ctx.sh(
                name=f"compile-{src.stem}",
                cmd=f"cp {src} {obj_path}",
                inputs=[str(src)], outputs=[obj_path],
            ))

        ctx.sh(
            name="link",
            cmd=f"cat {' '.join(o.outputs[0] for o in objects)} > build/app",
            inputs=objects + [o.outputs[0] for o in objects],
            outputs=["build/app"],
        )
        """,
    )

    _write_file(
        tmp_path / "cook.toml",
        """\
        [cook]
        default = "*"
        """,
    )

    (tmp_path / "build").mkdir()
    return tmp_path


def test_first_build_all_tasks_run(c_project: Path) -> None:
    result = _run(["exec", "*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "compile-bar" in result.stderr
    assert "compile-foo" in result.stderr
    assert "link" in result.stderr
    assert "Cooked" in result.stderr
    # Outputs created
    assert (c_project / "src" / "bar.o").exists()
    assert (c_project / "src" / "foo.o").exists()
    assert (c_project / "build" / "app").exists()


def test_incremental_no_changes(c_project: Path) -> None:
    # First build
    r1 = _run(["exec", "*"], c_project)
    assert r1.returncode == 0, r1.stderr

    # Second build: everything up-to-date
    r2 = _run(["exec", "*"], c_project)
    assert r2.returncode == 0, r2.stderr
    assert "Fresh" in r2.stderr
    # No "Cooked" in second run
    assert "Cooked" not in r2.stderr


def test_source_change_selective_rebuild(c_project: Path) -> None:
    # First build
    r1 = _run(["exec", "*"], c_project)
    assert r1.returncode == 0, r1.stderr

    # Modify foo.c
    time.sleep(0.05)  # ensure mtime differs
    (c_project / "src" / "foo.c").write_text("int foo() { return 42; }\n")

    # Second build: compile-foo and link re-run, compile-bar skipped
    r2 = _run(["exec", "*"], c_project)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  compile-foo" in r2.stderr
    assert "Cooked  link" in r2.stderr
    assert "Fresh   compile-bar" in r2.stderr


def test_output_deleted_rebuild(c_project: Path) -> None:
    # First build
    r1 = _run(["exec", "*"], c_project)
    assert r1.returncode == 0, r1.stderr

    # Delete foo.o
    (c_project / "src" / "foo.o").unlink()

    # Second build: compile-foo re-runs
    r2 = _run(["exec", "*"], c_project)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  compile-foo" in r2.stderr
    assert (c_project / "src" / "foo.o").exists()


def test_inspect_shows_graph(c_project: Path) -> None:
    result = _run(["inspect", "*"], c_project)
    assert result.returncode == 0, result.stderr
    # All tasks listed as STALE before any build
    assert "[compile-bar] STALE" in result.stdout
    assert "[compile-foo] STALE" in result.stdout
    assert "[link] STALE" in result.stdout
    assert "deps:" in result.stdout


def test_inspect_after_build(c_project: Path) -> None:
    _run(["exec", "*"], c_project, check=True)
    result = _run(["inspect", "*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "up-to-date" in result.stdout  # inspect to stdout


def test_dry_run_before_build(c_project: Path) -> None:
    """Dry run with no store: everything is stale."""
    result = _run(["exec", "--dry-run", "*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "STALE (would run)" in result.stderr
    # Nothing should be created
    assert not (c_project / "src" / "foo.o").exists()


def test_dry_run_after_build(c_project: Path) -> None:
    """Dry run after a successful build: everything up-to-date."""
    _run(["exec", "*"], c_project, check=True)

    result = _run(["exec", "--dry-run", "*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "up-to-date" in result.stderr
    assert "STALE" not in result.stderr


def test_dry_run_missing_output(c_project: Path) -> None:
    """Dry run detects missing output files as stale."""
    _run(["exec", "*"], c_project, check=True)

    # Delete an output
    (c_project / "src" / "foo.o").unlink()

    result = _run(["exec", "--dry-run", "*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "[compile-foo] STALE (would run)" in result.stderr
    # foo.o should NOT be recreated (dry-run)
    assert not (c_project / "src" / "foo.o").exists()


def test_invalidate_forces_rerun(c_project: Path) -> None:
    # Build first
    _run(["exec", "*"], c_project, check=True)

    # Invalidate compile-foo
    r_inv = _run(["invalidate", "compile-foo"], c_project)
    assert r_inv.returncode == 0, r_inv.stderr
    assert "Invalidated [compile-foo]" in r_inv.stderr

    # Re-run: compile-foo should re-run
    r2 = _run(["exec", "*"], c_project)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  compile-foo" in r2.stderr


def test_pattern_matching_only_compile(c_project: Path) -> None:
    result = _run(["exec", "compile-*"], c_project)
    assert result.returncode == 0, result.stderr
    assert "Cooked  compile-foo" in result.stderr
    assert "Cooked  compile-bar" in result.stderr
    # link should NOT appear
    assert "link" not in result.stderr


def test_always_run_task_no_outputs(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="check", cmd="true")
        """,
    )

    # First run
    r1 = _run(["exec", "check"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    assert "Cooked  check" in r1.stderr

    # Second run: still runs (no outputs = always-run)
    r2 = _run(["exec", "check"], tmp_path)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  check" in r2.stderr


def test_none_propagation_always_run_dep(tmp_path: Path) -> None:
    outfile = tmp_path / "out.txt"
    _write_file(
        tmp_path / "recipe.py",
        f"""\
        from cook import get_context
        ctx = get_context()
        check = ctx.sh(name="check", cmd="true")
        ctx.sh(name="build", cmd="echo done > {outfile}", inputs=[check], outputs=["{outfile}"])
        """,
    )

    # First run
    r1 = _run(["exec", "*"], tmp_path)
    assert r1.returncode == 0, r1.stderr
    assert "Cooked  check" in r1.stderr
    assert "Cooked  build" in r1.stderr

    # Second run: both re-run because check has no outputs (None propagation)
    r2 = _run(["exec", "*"], tmp_path)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  check" in r2.stderr
    assert "Cooked  build" in r2.stderr


def test_command_change_triggers_rebuild(c_project: Path) -> None:
    # First build
    _run(["exec", "*"], c_project, check=True)

    # Change the recipe to add a flag (different command)
    _write_file(
        c_project / "recipe.py",
        """\
        from pathlib import Path
        from cook import get_context

        ctx = get_context()

        sources = sorted(Path("src").glob("*.c"))
        objects = []
        for src in sources:
            obj_path = str(src.with_suffix(".o"))
            objects.append(ctx.sh(
                name=f"compile-{src.stem}",
                cmd=f"cp -v {src} {obj_path}",
                inputs=[str(src)], outputs=[obj_path],
            ))

        ctx.sh(
            name="link",
            cmd=f"cat {' '.join(o.outputs[0] for o in objects)} > build/app",
            inputs=objects + [o.outputs[0] for o in objects],
            outputs=["build/app"],
        )
        """,
    )

    # Rebuild: compile tasks should re-run due to command change
    r2 = _run(["exec", "*"], c_project)
    assert r2.returncode == 0, r2.stderr
    assert "Cooked  compile-foo" in r2.stderr
    assert "Cooked  compile-bar" in r2.stderr


# ---------------------------------------------------------------------------
# Scenario: keep-going mode
# ---------------------------------------------------------------------------


def test_keep_going_both_attempted(tmp_path: Path) -> None:
    good_out = tmp_path / "good.txt"
    _write_file(
        tmp_path / "recipe.py",
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail-task", cmd="exit 1", outputs=["{tmp_path / "fail.txt"}"])
        ctx.sh(name="good-task", cmd="echo ok > {good_out}", outputs=["{good_out}"])
        """,
    )

    # With -k: both attempted, exit code 1
    r_k = _run(["exec", "-k", "*"], tmp_path)
    assert r_k.returncode == 1
    assert good_out.exists()
    assert "FAILED" in r_k.stderr


def test_without_keep_going_fails(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail-task", cmd="exit 1", outputs=["{tmp_path / "fail.txt"}"])
        ctx.sh(name="good-task", cmd="echo ok > {tmp_path / "good.txt"}", outputs=["{tmp_path / "good.txt"}"])
        """,
    )

    # Without -k: fails
    result = _run(["exec", "*"], tmp_path)
    assert result.returncode == 1


# ---------------------------------------------------------------------------
# Scenario: validation errors
# ---------------------------------------------------------------------------


def test_validation_error_cycle(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        """\
        from cook import get_context, Task
        ctx = get_context()
        a = Task(name="a", inputs=[], outputs=["a.txt"])
        b = Task(name="b", inputs=[a], outputs=["b.txt"])
        a.inputs.append(b)
        ctx.register(a)
        ctx.register(b)
        """,
    )

    result = _run(["exec", "*"], tmp_path)
    assert result.returncode == 1
    assert "cycle" in result.stderr.lower()


def test_validation_error_duplicate_outputs(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task-a", cmd="touch out.txt", outputs=["out.txt"])
        ctx.sh(name="task-b", cmd="touch out.txt", outputs=["out.txt"])
        """,
    )

    result = _run(["exec", "*"], tmp_path)
    assert result.returncode == 1
    assert "duplicate output" in result.stderr.lower()


def test_validation_error_unregistered_dep(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        """\
        from cook import get_context, Task
        ctx = get_context()
        unregistered = Task(name="ghost", inputs=[], outputs=["ghost.txt"])
        ctx.sh(name="task-a", cmd="true", inputs=[unregistered], outputs=["a.txt"])
        """,
    )

    result = _run(["exec", "*"], tmp_path)
    assert result.returncode == 1
    assert "not registered" in result.stderr.lower()


# ---------------------------------------------------------------------------
# Additional edge cases
# ---------------------------------------------------------------------------


def test_default_pattern_from_config(tmp_path: Path) -> None:
    """cook exec (no pattern) uses default from cook.toml."""
    outfile = tmp_path / "out.txt"
    _write_file(
        tmp_path / "cook.toml",
        """\
        [cook]
        default = "my-task"
        """,
    )
    _write_file(
        tmp_path / "recipe.py",
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="my-task", cmd="echo hi > {outfile}", outputs=["{outfile}"])
        ctx.sh(name="other", cmd="true")
        """,
    )

    result = _run(["exec"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert outfile.exists()


def test_no_pattern_no_default_errors(tmp_path: Path) -> None:
    _write_file(
        tmp_path / "recipe.py",
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true")
        """,
    )

    result = _run(["exec"], tmp_path)
    assert result.returncode == 1
    assert "no target pattern" in result.stderr.lower()


def test_no_recipe_file_errors(tmp_path: Path) -> None:
    result = _run(["exec", "*"], tmp_path)
    assert result.returncode == 1
    assert "recipe" in result.stderr.lower()


def test_dry_run_no_store(tmp_path: Path) -> None:
    """Dry run with no .cook.db: everything is stale."""
    outfile = tmp_path / "out.txt"
    _write_file(
        tmp_path / "recipe.py",
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )

    result = _run(["exec", "--dry-run", "*"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "STALE (would run)" in result.stderr
    assert not outfile.exists()
