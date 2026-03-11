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
    rc = main(["exec", "hello"])
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
    rc = main(["exec", "compile-*"])
    assert rc == 0
    assert out_a.exists()
    assert out_b.exists()
    assert not out_other.exists()


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
    rc = main(["exec"])
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
    rc = main(["exec"])
    assert rc == 1
    assert "No target pattern" in capsys.readouterr().out


def test_exec_no_matches(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="task1", cmd="true", outputs=[])
        """,
    )
    rc = main(["exec", "nonexistent-*"])
    assert rc == 1
    assert "matched no tasks" in capsys.readouterr().out


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
    rc = main(["exec", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "build"])
    assert rc == 0
    capsys.readouterr()

    # Now dry run should show up-to-date
    rc = main(["exec", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "mytask"])
    assert rc == 0
    capsys.readouterr()

    # Inspect should show up-to-date
    rc = main(["inspect", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "up-to-date" in captured


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
    rc = main(["exec", "mytask"])
    assert rc == 0
    capsys.readouterr()

    # Invalidate
    rc = main(["invalidate", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "Invalidated [mytask]" in captured

    # Run again — should re-execute (not up-to-date)
    rc = main(["exec", "mytask"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "started" in captured


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
    rc = main(["exec", "-k", "*"])
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
    rc = main(["exec", "*"])
    assert rc == 1
    captured = capsys.readouterr().out
    assert "cycle" in captured.lower()


def test_recipe_import_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        this is not valid python!!!
        """,
    )
    rc = main(["exec", "*"])
    assert rc == 1
    captured = capsys.readouterr().out
    assert "Error loading recipe" in captured


def test_recipe_not_found(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # No recipe.py created
    rc = main(["exec", "*"])
    assert rc == 1
    captured = capsys.readouterr().out
    assert "Error loading recipe" in captured


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
    assert main(["exec", "ok"]) == 0


def test_exit_code_failure(project: Path) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail", cmd="exit 1", outputs=["nope.txt"])
        """,
    )
    assert main(["exec", "fail"]) == 1


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
    rc = main(["exec", "-j", "2", "t"])
    assert rc == 0
    assert outfile.exists()


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
    rc = main(["exec", "-n", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "--executor", "slurm", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().out.lower()


def test_executor_short_flag(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["exec", "-x", "slurm", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().out.lower()


def test_unknown_executor_config(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "cook.toml").write_text('[cook]\nexecutor = "slurm"\n')
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="true", outputs=[])
        """,
    )
    rc = main(["exec", "t"])
    assert rc == 1
    assert "unknown executor" in capsys.readouterr().out.lower()


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
    rc = main(["exec", "leaf-task"])
    assert rc == 0
    assert out_dep.exists()
    assert out_leaf.exists()


def test_config_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "cook.toml").write_text("this is not valid toml {{{")
    _write_recipe(project, "from cook import get_context\nctx = get_context()\n")
    rc = main(["exec", "*"])
    assert rc == 1
    captured = capsys.readouterr().out
    assert "Error" in captured


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
    rc = main(["exec", "--dry-run", "leaf"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    assert "matched no tasks" in capsys.readouterr().out


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
    assert "No target pattern" in capsys.readouterr().out


def test_inspect_recipe_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "def\n")
    rc = main(["inspect", "*"])
    assert rc == 1
    assert "Error loading recipe" in capsys.readouterr().out


def test_recipe_runtime_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "raise RuntimeError('boom')\n")
    rc = main(["exec", "*"])
    assert rc == 1
    assert "Error loading recipe" in capsys.readouterr().out


def test_recipe_name_error(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_recipe(project, "undefined_variable\n")
    rc = main(["exec", "*"])
    assert rc == 1
    assert "Error loading recipe" in capsys.readouterr().out


def test_invalidate_recipe_error(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_recipe(project, "def\n")
    rc = main(["invalidate", "*"])
    assert rc == 1
    assert "Error loading recipe" in capsys.readouterr().out


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
    rc = main(["exec", "always-run"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "started" in captured


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
    rc = main(["exec", "*"])
    assert rc == 0
    capsys.readouterr()

    # Dry run: always-run has no outputs -> stale, leaf depends on stale dep -> stale
    rc = main(["exec", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "build"])
    assert rc == 0
    capsys.readouterr()

    # Delete the output
    outfile.unlink()

    # Dry run should detect it's stale
    rc = main(["exec", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "[shared] STALE (would run)" in captured


def test_load_recipe_empty_sys_path(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cover the empty sys.path branch."""
    from cook.cli import _load_recipe

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
            _load_recipe(str(project / "recipe.py"))
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
    from cook.cli import _load_recipe

    # A directory can't be loaded as a module
    with pytest.raises(ImportError, match="Cannot load recipe"):
        _load_recipe(str(project))


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

    rc = main(["exec", "--dry-run", "new-task"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    rc = main(["exec", "build"])
    assert rc == 0
    assert outfile.read_text().strip() == "v1"
    capsys.readouterr()

    # Modify the source file
    infile.write_text("v2")

    # Dry run should detect staleness
    rc = main(["exec", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "STALE (would run)" in captured


def test_validate_bad_recipe(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Validate with a broken recipe prints an error."""
    _write_recipe(project, "raise RuntimeError('boom')")
    rc = main(["validate", "*"])
    assert rc == 1
    assert "Error loading recipe" in capsys.readouterr().out


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
    captured = capsys.readouterr().out
    assert "[build] validated" in captured

    # Now dry-run should show up-to-date
    rc = main(["exec", "--dry-run", "build"])
    assert rc == 0
    captured = capsys.readouterr().out
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
    captured = capsys.readouterr().out
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
    captured = capsys.readouterr().out
    assert "[build] skipped" in captured
    assert "missing outputs" in captured


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
    captured = capsys.readouterr().out
    assert "[dep] validated" in captured
    assert "[main] validated" in captured

    # Both should be up-to-date now
    rc = main(["exec", "--dry-run", "*"])
    assert rc == 0
    captured = capsys.readouterr().out
    assert "up-to-date" in captured
    assert "STALE" not in captured
