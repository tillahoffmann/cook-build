from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cook.cli import main
from cook.context import Context


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a temp project directory and chdir into it."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_recipe(project: Path, code: str, name: str = "recipe.py") -> Path:
    recipe = project / name
    recipe.write_text(textwrap.dedent(code))
    return recipe


def test_exec_basic(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="hello", cmd="echo hello > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "hello"])
    assert rc == 0
    assert outfile.exists()
    assert outfile.read_text().strip() == "hello"


def test_exec_pattern_matching(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    out_other = project / "other.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="compile-a", cmd="echo a > {out_a}", outputs=["{out_a}"])
        ctx.sh(name="compile-b", cmd="echo b > {out_b}", outputs=["{out_b}"])
        ctx.sh(name="test-all", cmd="echo other > {out_other}", outputs=["{out_other}"])
        """,
    )
    rc = main(["run", "compile-*"])
    assert rc == 0
    assert out_a.exists()
    assert out_b.exists()
    assert not out_other.exists()


def test_exec_multiple_patterns(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    out_c = project / "c.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="compile-a", cmd="echo a > {out_a}", outputs=["{out_a}"])
        ctx.sh(name="test-b", cmd="echo b > {out_b}", outputs=["{out_b}"])
        ctx.sh(name="lint-c", cmd="echo c > {out_c}", outputs=["{out_c}"])
        """,
    )
    rc = main(["run", "compile-*", "test-*"])
    assert rc == 0
    assert out_a.exists()
    assert out_b.exists()
    assert not out_c.exists()


def test_exec_regex_pattern(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    out_other = project / "other.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="compile-a", cmd="echo a > {out_a}", outputs=["{out_a}"])
        ctx.sh(name="compile-b", cmd="echo b > {out_b}", outputs=["{out_b}"])
        ctx.sh(name="test-all", cmd="echo other > {out_other}", outputs=["{out_other}"])
        """,
    )
    rc = main(["run", "--re", "^compile-"])
    assert rc == 0
    assert out_a.exists()
    assert out_b.exists()
    assert not out_other.exists()


def test_exec_regex_invalid(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["run", "--re", "[invalid"])
    assert rc == 1
    assert "Invalid regex" in capsys.readouterr().err


def test_exec_with_default_config(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    (project / "cook.toml").write_text('[cook]\ndefault = "hello"\n')
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="hello", cmd="echo hi > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run"])
    assert rc == 0
    assert outfile.exists()


def test_exec_no_pattern_no_default(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true", outputs=[])
        """,
    )
    rc = main(["run"])
    assert rc == 1
    assert "No target pattern" in capsys.readouterr().err


def test_exec_no_matches(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true", outputs=[])
        """,
    )
    rc = main(["run", "nonexistent-*"])
    assert rc == 1
    assert "matched no tasks" in capsys.readouterr().err


def test_exec_dry_run(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "STALE (would run)" in captured
    # File should NOT be created
    assert not outfile.exists()


def test_exec_dry_run_with_existing_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # First run to populate store
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    # Now dry run should show up-to-date
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "up-to-date" in captured


def test_inspect(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        a = ctx.sh(name="step-a", cmd="echo a > {out_a}", outputs=["{out_a}"])
        ctx.sh(name="step-b", cmd="echo b > {out_b}", inputs=[a], outputs=["{out_b}"])
        """,
    )
    rc = main(["inspect", "*"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "[step-a] STALE" in captured
    assert "[step-b] STALE" in captured
    assert "deps: step-a" in captured


def test_inspect_shows_never_run(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fresh-task", cmd="true", outputs=["out.txt"])
        """,
    )
    rc = main(["inspect", "fresh-task"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "never run" in captured


def test_inspect_shows_why_always_run(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="check", cmd="true")
        """,
    )
    rc = main(["inspect", "check"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "always-run" in captured


def test_inspect_shows_why_output_missing(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run first, then delete output
    rc = main(["run", "build"])
    assert rc == 0
    outfile.unlink()
    capsys.readouterr()

    rc = main(["inspect", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "output missing" in captured


def test_inspect_shows_why_always_run_with_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Always-run reason via staleness_reason (store exists)."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        ctx.sh(name="check", cmd="true")
        """,
    )
    # Run build to create the store, then inspect check
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["inspect", "check"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "always-run" in captured


def test_inspect_shows_never_run_with_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Never-run reason via staleness_reason (store exists but no record)."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="built", cmd="echo x > {outfile}", outputs=["{outfile}"])
        ctx.sh(name="new-task", cmd="true", outputs=["new.txt"])
        """,
    )
    # Run only built to create the store
    rc = main(["run", "built"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["inspect", "new-task"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "never run" in captured


def test_inspect_shows_why_dep_stale(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    infile = project / "src.txt"
    infile.write_text("original")
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        a = ctx.sh(name="step-a", cmd="cat {infile} > {out_a}", inputs=["{infile}"], outputs=["{out_a}"])
        ctx.sh(name="step-b", cmd="cat {out_a} > {out_b}", inputs=[a], outputs=["{out_b}"])
        """,
    )
    rc = main(["run", "*"])
    assert rc == 0
    capsys.readouterr()

    # Modify input to make step-a stale
    infile.write_text("changed")
    rc = main(["inspect", "step-b"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "dependency" in captured
    assert "step-a" in captured


def test_inspect_shows_why_input_missing(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    infile = project / "src.txt"
    infile.write_text("data")
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="cat {infile} > {outfile}", inputs=["{infile}"], outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    # Delete the input file
    infile.unlink()
    rc = main(["inspect", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "input missing" in captured


def test_inspect_shows_why_digest_changed(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    infile = project / "src.txt"
    infile.write_text("original")
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="cat {infile} > {outfile}", inputs=["{infile}"], outputs=["{outfile}"])
        """,
    )
    # Run first
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    # Modify input
    infile.write_text("changed")
    rc = main(["inspect", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "digest changed" in captured


def test_inspect_with_store(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="mytask", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run first to populate store
    rc = main(["run", "mytask"])
    assert rc == 0
    capsys.readouterr()

    # Inspect should show up-to-date
    rc = main(["inspect", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "up-to-date" in captured


def test_format_relative_time() -> None:
    from datetime import datetime, timedelta, timezone

    from cook.cli.util import format_relative_time

    now = datetime.now(timezone.utc)
    assert "s ago" in format_relative_time(now - timedelta(seconds=30))
    assert "m ago" in format_relative_time(now - timedelta(minutes=5))
    assert "h ago" in format_relative_time(now - timedelta(hours=3))
    assert "d ago" in format_relative_time(now - timedelta(days=2))


def test_inspect_shows_details(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inspect shows inputs, outputs, command, and execution history."""
    infile = project / "src.txt"
    infile.write_text("data")
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(
            name="build",
            cmd="cat {infile} > {outfile}",
            inputs=["{infile}"],
            outputs=["{outfile}"],
        )
        """,
    )
    # Run to populate store
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["inspect", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "[build] up-to-date" in captured
    assert "inputs:" in captured
    assert "outputs:" in captured
    assert "cmd:" in captured
    assert "last started:" in captured
    assert "last succeeded:" in captured


def test_inspect_shows_failure_history(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Inspect shows last failed and error message."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="bad", cmd="echo oops >&2; exit 1", outputs=["nope.txt"])
        """,
    )
    # Run and fail
    rc = main(["run", "bad"])
    assert rc == 1
    capsys.readouterr()

    rc = main(["inspect", "bad"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "[bad] STALE" in captured
    assert "last failed:" in captured
    assert "error:" in captured


def test_invalidate(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="mytask", cmd="echo hello > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run first
    rc = main(["run", "mytask"])
    assert rc == 0
    capsys.readouterr()

    # Invalidate
    rc = main(["invalidate", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "Invalidated [mytask]" in captured

    # Run again — should re-execute (not up-to-date)
    rc = main(["run", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "Cooked" in captured


def test_invalidate_no_store(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Invalidate without .cook.db should succeed with a message."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
    )
    rc = main(["invalidate", "t"])
    assert rc == 0
    assert "nothing to invalidate" in capsys.readouterr().err.lower()


def test_keep_going(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    good_out = project / "good.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail-task", cmd="exit 1", outputs=["{project / "nope.txt"}"])
        ctx.sh(name="good-task", cmd="echo ok > {good_out}", outputs=["{good_out}"])
        """,
    )
    rc = main(["run", "-k", "*"])
    assert rc == 1
    assert good_out.exists()


def test_validation_error_cycle(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
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
    rc = main(["run", "*"])
    assert rc == 1
    captured = capsys.readouterr().err
    assert "cycle" in captured.lower()


def test_recipe_import_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        this is not valid python!!!
        """,
    )
    rc = main(["run", "*"])
    assert rc == 1
    captured = capsys.readouterr().err
    assert "error: loading recipe" in captured


def test_recipe_not_found(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # No recipe.py created
    rc = main(["run", "*"])
    assert rc == 1
    captured = capsys.readouterr().err
    assert "error: loading recipe" in captured


def test_exit_code_success(project: Path) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="ok", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    assert main(["run", "ok"]) == 0


def test_exit_code_failure(project: Path) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail", cmd="exit 1", outputs=["nope.txt"])
        """,
    )
    assert main(["run", "fail"]) == 1


def test_no_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main([])
    assert rc == 1


def test_jobs_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "-j", "2", "t"])
    assert rc == 0
    assert outfile.exists()


def test_dry_run_does_not_create_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run should not create or modify the .cook.db file."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    db_path = project / ".cook.db"
    assert not db_path.exists()
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    assert not db_path.exists()


def test_dry_run_does_not_modify_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry-run with existing store should not modify any records."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Build first to populate store
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    # Record the store state
    from cook.store.sqlite import SqliteBuildStore

    with SqliteBuildStore(str(project / ".cook.db")) as store:
        record_before = store.get("build")
    assert record_before is not None

    # Modify input to make task stale, then dry-run
    outfile.unlink()
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    assert "STALE" in capsys.readouterr().err

    # Store record should be unchanged
    with SqliteBuildStore(str(project / ".cook.db")) as store:
        record_after = store.get("build")
    assert record_after is not None
    assert record_after.digest == record_before.digest
    assert record_after.last_started == record_before.last_started


def test_dry_run_short_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "-n", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "STALE (would run)" in captured
    assert not outfile.exists()


def test_unknown_executor_flag(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["run", "--executor", "nosuch", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().err.lower()


def test_executor_short_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["run", "-x", "nosuch", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().err.lower()


def test_unknown_executor_config(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "cook.toml").write_text('[cook]\nexecutor = "nosuch"\n')
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["run", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().err.lower()


def test_exec_slurm_executor_config(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI instantiates SlurmExecutor with config from cook.toml."""
    (project / "cook.toml").write_text(
        '[cook]\nexecutor = "slurm"\n\n'
        "[cook.slurm]\nmax_concurrent = 16\npoll_interval = 1.5\npoll_timeout = 7200\n"
    )
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    # sbatch is not available locally, so task fails — but the slurm branch is exercised
    rc = main(["run", "t"])
    assert rc == 1
    output = capsys.readouterr().err.lower()
    assert "sbatch" in output


def test_exec_with_dependencies(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pattern matches only leaf task, but dependencies run too."""
    out_dep = project / "dep.txt"
    out_leaf = project / "leaf.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        dep = ctx.sh(name="dep-task", cmd="echo dep > {out_dep}", outputs=["{out_dep}"])
        ctx.sh(name="leaf-task", cmd="echo leaf > {out_leaf}", inputs=[dep], outputs=["{out_leaf}"])
        """,
    )
    rc = main(["run", "leaf-task"])
    assert rc == 0
    assert out_dep.exists()
    assert out_leaf.exists()


def test_config_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "cook.toml").write_text("this is not valid toml {{{")
    _write_recipe(project, "from cook import get_context\nctx = get_context()\n")
    rc = main(["run", "*"])
    assert rc == 1
    captured = capsys.readouterr().err
    assert "error" in captured.lower()


def test_dry_run_with_deps(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_a = project / "a.txt"
    out_b = project / "b.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        a = ctx.sh(name="dep", cmd="echo a > {out_a}", outputs=["{out_a}"])
        ctx.sh(name="leaf", cmd="echo b > {out_b}", inputs=[a], outputs=["{out_b}"])
        """,
    )
    rc = main(["run", "--dry-run", "leaf"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[dep] STALE (would run)" in captured
    assert "[leaf] STALE (would run)" in captured


def test_invalidate_no_matches(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true", outputs=[])
        """,
    )
    rc = main(["invalidate", "nonexistent-*"])
    assert rc == 1
    assert "matched no tasks" in capsys.readouterr().err


def test_inspect_no_pattern_no_default(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true", outputs=[])
        """,
    )
    rc = main(["inspect"])
    assert rc == 1
    assert "No target pattern" in capsys.readouterr().err


def test_inspect_recipe_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "def\n")
    rc = main(["inspect", "*"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err


def test_recipe_runtime_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "raise RuntimeError('boom')\n")
    rc = main(["run", "*"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err


def test_recipe_name_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(project, "undefined_variable\n")
    rc = main(["run", "*"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err


def test_invalidate_recipe_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "def\n")
    rc = main(["invalidate", "*"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err


def test_exec_no_output_task(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Tasks with no outputs always run."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="always-run", cmd="true")
        """,
    )
    rc = main(["run", "always-run"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "Cooked" in captured


def test_dry_run_no_output_task(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry run shows no-output tasks as stale."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        dep = ctx.sh(name="always-run", cmd="true")
        ctx.sh(name="leaf", cmd="echo x > {outfile}", inputs=[dep], outputs=["{outfile}"])
        """,
    )
    # First run to populate store (creates .cook.db)
    rc = main(["run", "*"])
    assert rc == 0
    capsys.readouterr()

    # Dry run: always-run has no outputs -> stale, leaf depends on stale dep -> stale
    rc = main(["run", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[always-run] STALE (would run)" in captured
    assert "[leaf] STALE (would run)" in captured


def test_dry_run_missing_output(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Dry run detects missing output files."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run to populate store
    rc = main(["run", "build"])
    assert rc == 0
    capsys.readouterr()

    # Delete the output
    outfile.unlink()

    # Dry run should detect it's stale
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "STALE (would run)" in captured


def test_dry_run_staleness_cache(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Diamond deps: staleness cache is hit for shared dependency."""
    out_d = project / "d.txt"
    out_b = project / "b.txt"
    out_c = project / "c.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        d = ctx.sh(name="shared", cmd="echo d > {out_d}", outputs=["{out_d}"])
        ctx.sh(name="left", cmd="echo b > {out_b}", inputs=[d], outputs=["{out_b}"])
        ctx.sh(name="right", cmd="echo c > {out_c}", inputs=[d], outputs=["{out_c}"])
        """,
    )
    rc = main(["run", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[shared] STALE (would run)" in captured


def test_load_recipe_empty_sys_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover the empty sys.path branch."""
    from cook.cli.util import load_recipe

    _write_recipe(
        project,
        """\
        # empty recipe
        """,
    )
    original_path = sys.path.copy()
    sys.path.clear()
    try:
        with Context():
            load_recipe(str(project / "recipe.py"))
    finally:
        sys.path.clear()
        sys.path.extend(original_path)


def test_main_module_block() -> None:
    """Cover the if __name__ == '__main__' block via runpy."""

    result = subprocess.run(
        [sys.executable, "-m", "cook.cli"],
        capture_output=True,
        text=True,
    )
    # Should print help and exit 1 (no subcommand)
    assert result.returncode == 1


def test_load_recipe_spec_none(
    project: Path,
) -> None:
    """Cover the spec is None branch."""
    from cook.cli.util import load_recipe

    # A directory can't be loaded as a module
    with pytest.raises(ImportError, match="Cannot load recipe"):
        load_recipe(str(project))


def test_dry_run_no_record_in_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Store exists but task has no record -> stale."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="new-task", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Create a store with some other task so .cook.db exists
    from cook.store.sqlite import SqliteBuildStore

    store = SqliteBuildStore(str(project / ".cook.db"))
    store.close()

    rc = main(["run", "--dry-run", "new-task"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "STALE (would run)" in captured


def test_main_as_script(project: Path) -> None:
    """Cover the if __name__ == '__main__' block."""
    # We can't easily cover this in-process, so just verify the module attribute
    import cook.cli

    assert hasattr(cook.cli, "main")


def test_dry_run_detects_modified_source(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """After modifying a source file, dry-run should show the task as stale."""
    infile = project / "src.txt"
    infile.write_text("v1")
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="cat {infile} > {outfile}", inputs=["{infile}"], outputs=["{outfile}"])
        """,
    )
    # Build successfully
    rc = main(["run", "build"])
    assert rc == 0
    assert outfile.read_text().strip() == "v1"
    capsys.readouterr()

    # Modify the source file
    infile.write_text("v2")

    # Dry run should detect staleness
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "STALE (would run)" in captured


def test_validate_bad_recipe(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Validate with a broken recipe prints an error."""
    _write_recipe(project, "raise RuntimeError('boom')")
    rc = main(["validate", "*"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err  # validate bad recipe


def test_validate_marks_task_up_to_date(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validate stores the effective digest so the task is up-to-date."""
    outfile = project / "out.txt"
    outfile.write_text("already built")
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["validate", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[build] validated" in captured

    # Now dry-run should show up-to-date
    rc = main(["run", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "up-to-date" in captured


def test_validate_skips_no_output_task(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tasks with no outputs cannot be validated."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="check", cmd="true")
        """,
    )
    rc = main(["validate", "check"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[check] skipped" in captured
    assert "no outputs" in captured


def test_validate_skips_missing_outputs(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Validate skips tasks whose output files don't exist."""
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build", cmd="echo x", outputs=["{project / "ghost.txt"}"])
        """,
    )
    rc = main(["validate", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[build] skipped" in captured
    assert "missing outputs" in captured


def test_validate_skips_always_run_dependency(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tasks depending on always-run tasks cannot be validated."""
    outfile = project / "out.txt"
    outfile.write_text("done")
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        check = ctx.sh(name="check", cmd="true")
        ctx.sh(name="build", cmd="true", inputs=[check], outputs=["{outfile}"])
        """,
    )
    rc = main(["validate", "build"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[build] skipped" in captured
    assert "always-run" in captured


def test_validate_with_deps(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Validate processes deps first so dependent digests are correct."""
    dep_out = project / "dep.txt"
    dep_out.write_text("dep done")
    main_out = project / "main.txt"
    main_out.write_text("main done")
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        dep = ctx.sh(name="dep", cmd="true", outputs=["{dep_out}"])
        ctx.sh(
            name="main",
            cmd="true",
            inputs=[dep],
            outputs=["{main_out}"],
        )
        """,
    )
    rc = main(["validate", "main"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "[dep] validated" in captured
    assert "[main] validated" in captured

    # Both should be up-to-date now
    rc = main(["run", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().err
    assert "up-to-date" in captured
    assert "STALE" not in captured


# --- ls command ---


def test_ls_lists_all_tasks(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="alpha", cmd="true")
        ctx.sh(name="beta", cmd="true")
        ctx.sh(name="gamma", cmd="true")
        """,
    )
    rc = main(["list"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert sorted(lines) == ["alpha", "beta", "gamma"]


def test_ls_with_pattern(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build-a", cmd="true")
        ctx.sh(name="build-b", cmd="true")
        ctx.sh(name="test-a", cmd="true")
        """,
    )
    rc = main(["list", "build-*"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert sorted(lines) == ["build-a", "build-b"]


def test_ls_with_regex(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="build-a", cmd="true")
        ctx.sh(name="build-b", cmd="true")
        ctx.sh(name="test-a", cmd="true")
        """,
    )
    rc = main(["list", "--re", "^build-"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert sorted(lines) == ["build-a", "build-b"]


def test_ls_stale_filter(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="has-out", cmd="echo x > {outfile}", outputs=["{outfile}"])
        ctx.sh(name="no-out", cmd="true")
        """,
    )
    # Run to make has-out current
    rc = main(["run", "has-out"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["list", "--stale"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    # no-out has no outputs, always stale; has-out was just built
    assert "no-out" in lines
    assert "has-out" not in lines


def test_ls_current_filter(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="has-out", cmd="echo x > {outfile}", outputs=["{outfile}"])
        ctx.sh(name="no-out", cmd="true")
        """,
    )
    # Run to make has-out current
    rc = main(["run", "has-out"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["list", "--current"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert "has-out" in lines
    assert "no-out" not in lines


def test_ls_stale_no_store(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without a store, --stale lists all tasks."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="a", cmd="true")
        ctx.sh(name="b", cmd="true")
        """,
    )
    rc = main(["list", "--stale"])
    assert rc == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert sorted(lines) == ["a", "b"]


def test_ls_current_no_store(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without a store, --current lists nothing."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="a", cmd="true")
        """,
    )
    rc = main(["list", "--current"])
    assert rc == 0
    captured = capsys.readouterr().out.strip()
    assert captured == ""


def test_ls_recipe_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(project, "raise RuntimeError('boom')")
    rc = main(["list"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err  # ls recipe error


def test_ls_pattern_no_match(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true")
        """,
    )
    rc = main(["list", "nonexistent-*"])
    assert rc == 1
    assert "matched no tasks" in capsys.readouterr().err


# --- -f / --file flag ---


def test_file_flag(project: Path) -> None:
    """The -f flag overrides the recipe file."""
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="custom", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
        name="custom.py",
    )
    rc = main(["-f", "custom.py", "run", "custom"])
    assert rc == 0
    assert outfile.exists()


def test_file_long_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
        name="other.py",
    )
    rc = main(["--file", "other.py", "list"])
    assert rc == 0
    assert "t" in capsys.readouterr().out


def test_file_flag_inspect(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
        name="alt.py",
    )
    rc = main(["-f", "alt.py", "inspect", "t"])
    assert rc == 0
    assert "[t]" in capsys.readouterr().out


def test_file_flag_invalidate(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo x > {outfile}", outputs=["{outfile}"])
        """,
        name="alt.py",
    )
    # Run first to have something to invalidate
    rc = main(["-f", "alt.py", "run", "t"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["-f", "alt.py", "invalidate", "t"])
    assert rc == 0
    assert "Invalidated" in capsys.readouterr().err


def test_file_flag_validate(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    outfile.write_text("done")
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=["{outfile}"])
        """,
        name="alt.py",
    )
    rc = main(["-f", "alt.py", "validate", "t"])
    assert rc == 0
    assert "validated" in capsys.readouterr().err


def test_file_flag_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["-f", "nonexistent.py", "list"])
    assert rc == 1
    assert "error: loading recipe" in capsys.readouterr().err  # file flag missing


# --- -c / --config flag ---


def test_config_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The -c flag loads config from a custom file."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
        name="my_recipe.py",
    )
    config = project / "dev.toml"
    config.write_text('[cook]\nrecipe = "my_recipe.py"\n')
    rc = main(["-c", str(config), "list"])
    assert rc == 0
    assert "t" in capsys.readouterr().out


def test_config_flag_missing(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["-c", "nonexistent.toml", "list"])
    assert rc == 1
    assert "Config file not found" in capsys.readouterr().err


def test_config_flag_file_overrides(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """-f takes precedence over recipe in config file."""
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="from-flag", cmd="true")
        """,
        name="flag.py",
    )
    config = project / "dev.toml"
    config.write_text('[cook]\nrecipe = "wrong.py"\n')
    rc = main(["-c", str(config), "-f", "flag.py", "list"])
    assert rc == 0
    assert "from-flag" in capsys.readouterr().out


# --- verbosity and color flags ---


def test_verbose_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["-v", "run", "t"])
    assert rc == 0
    err = capsys.readouterr().err
    # Verbose mode shows the command
    assert f"$ echo ok > {outfile}" in err


def test_quiet_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["-q", "run", "t"])
    assert rc == 0
    err = capsys.readouterr().err
    # Quiet mode: no "Cooked" line, but summary still shows
    assert "Cooked" not in err


def test_verbose_and_quiet_conflict(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["-v", "-q", "list"])
    assert rc == 1
    assert "Cannot use --verbose and --quiet" in capsys.readouterr().err


def test_color_never(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
    )
    rc = main(["--color", "never", "list"])
    assert rc == 0


def test_color_always(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
    )
    rc = main(["--color", "always", "list"])
    assert rc == 0


def test_stream_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["run", "-s", "t"])
    assert rc == 0


# --- build command ---


def test_build_by_output(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "result.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["build", str(outfile)])
    assert rc == 0
    assert outfile.exists()


def test_build_glob_pattern(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out1 = project / "a.o"
    out2 = project / "b.o"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="compile-a", cmd="echo a > {out1}", outputs=["{out1}"])
        ctx.sh(name="compile-b", cmd="echo b > {out2}", outputs=["{out2}"])
        ctx.sh(name="unrelated", cmd="true")
        """,
    )
    rc = main(["build", "*.o"])
    assert rc == 0
    assert out1.exists()
    assert out2.exists()
    err = capsys.readouterr().err
    # Both compile tasks ran, unrelated did not
    assert "compile-a" in err
    assert "compile-b" in err


def test_build_regex(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "output.dat"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["build", "-r", r"\.dat$"])
    assert rc == 0
    assert outfile.exists()


def test_build_no_match(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=["foo.txt"])
        """,
    )
    rc = main(["build", "*.xyz"])
    assert rc == 1
    assert "matched no task outputs" in capsys.readouterr().err


def test_build_no_pattern(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
    )
    rc = main(["build"])
    assert rc == 1
    assert "No output pattern" in capsys.readouterr().err


def test_build_dry_run(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    rc = main(["build", "-n", str(outfile)])
    assert rc == 0
    assert not outfile.exists()
    assert "STALE" in capsys.readouterr().err


def test_build_dry_run_with_store(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Build first to create the store
    rc = main(["build", str(outfile)])
    assert rc == 0
    capsys.readouterr()

    # Dry-run should show up-to-date
    rc = main(["build", "-n", str(outfile)])
    assert rc == 0
    assert "up-to-date" in capsys.readouterr().err


def test_build_regex_invalid(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=["foo.txt"])
        """,
    )
    rc = main(["build", "-r", "[invalid"])
    assert rc == 1
    assert "Invalid regex" in capsys.readouterr().err


def test_build_regex_no_match(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=["foo.txt"])
        """,
    )
    rc = main(["build", "-r", r"\.xyz$"])
    assert rc == 1
    assert "matched no task outputs" in capsys.readouterr().err


def test_build_multiple_patterns(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out1 = project / "a.o"
    out2 = project / "b.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t1", cmd="echo a > {out1}", outputs=["{out1}"])
        ctx.sh(name="t2", cmd="echo b > {out2}", outputs=["{out2}"])
        """,
    )
    rc = main(["build", "*.o", "*.txt"])
    assert rc == 0
    assert out1.exists()
    assert out2.exists()


def test_build_one_pattern_no_match(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=["foo.txt"])
        """,
    )
    rc = main(["build", "*.txt", "*.xyz"])
    assert rc == 1
    assert "*.xyz" in capsys.readouterr().err


# --- --json flag ---


def test_ls_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="alpha", cmd="true")
        ctx.sh(name="beta", cmd="true")
        """,
    )
    rc = main(["list", "--json"])
    assert rc == 0
    import json

    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    objs = [json.loads(line) for line in lines]
    names = {o["name"] for o in objs}
    assert names == {"alpha", "beta"}


def test_ls_json_with_stale(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true")
        """,
    )
    rc = main(["list", "--json", "--stale"])
    assert rc == 0
    import json

    lines = capsys.readouterr().out.strip().splitlines()
    obj = json.loads(lines[0])
    assert obj["name"] == "t"
    assert obj["stale"] is True


def test_inspect_json(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok", outputs=["{outfile}"])
        """,
    )
    rc = main(["inspect", "--json", "gen"])
    assert rc == 0
    import json

    lines = capsys.readouterr().out.strip().splitlines()
    obj = json.loads(lines[0])
    assert obj["name"] == "gen"
    assert obj["stale"] is True
    assert obj["cmd"] == "echo ok"
    assert str(outfile) in obj["outputs"]


def test_inspect_json_with_history(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="gen", cmd="echo ok > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run first to create history
    rc = main(["run", "gen"])
    assert rc == 0
    capsys.readouterr()

    rc = main(["inspect", "--json", "gen"])
    assert rc == 0
    import json

    lines = capsys.readouterr().out.strip().splitlines()
    obj = json.loads(lines[0])
    assert obj["name"] == "gen"
    assert obj["stale"] is False
    assert "history" in obj
    assert "last_succeeded" in obj["history"]
    assert "duration" in obj["history"]


def test_inspect_json_failed_task(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail", cmd="exit 1", outputs=["{outfile}"])
        """,
    )
    # Run to create a failure record
    rc = main(["run", "fail"])
    assert rc == 1
    capsys.readouterr()

    rc = main(["inspect", "--json", "fail"])
    assert rc == 0
    import json

    lines = capsys.readouterr().out.strip().splitlines()
    obj = json.loads(lines[0])
    assert obj["name"] == "fail"
    assert "history" in obj
    assert "last_failed" in obj["history"]
    assert "error" in obj["history"]
