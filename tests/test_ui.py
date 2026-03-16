from __future__ import annotations

import pytest

from cook.ui import Output, Style, Verbosity, _should_color


def test_should_color_override_true():
    assert _should_color(True) is True


def test_should_color_override_false():
    assert _should_color(False) is False


def test_should_color_no_color_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert _should_color(None) is False


def test_should_color_clicolor_force(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    assert _should_color(None) is True


def test_style_enabled():
    s = Style(True)
    result = s.green("hello")
    assert "\033[" in result
    assert "hello" in result


def test_style_disabled():
    s = Style(False)
    assert s.green("hello") == "hello"
    assert s.red("hello") == "hello"
    assert s.bold("hello") == "hello"


def test_style_all_methods():
    s = Style(True)
    for method in (s.green, s.red, s.yellow, s.cyan, s.dim, s.bold):
        result = method("test")
        assert "test" in result
        assert "\033[" in result


def test_output_task_fresh(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.set_total(3)
    ui.task_fresh("my-task")
    err = capsys.readouterr().err
    assert "[1/3]" in err
    assert "Fresh" in err
    assert "my-task" in err


def test_output_task_cooked(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.set_total(2)
    ui.task_cooked("build", 1.5)
    err = capsys.readouterr().err
    assert "[1/2]" in err
    assert "Cooked" in err
    assert "build" in err
    assert "(1.5s)" in err


def test_output_task_failed(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.set_total(1)
    ui.task_failed("broken", 0.3, "exit code 1")
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "broken" in err
    assert "exit code 1" in err


def test_output_task_failed_multiline(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.set_total(1)
    ui.task_failed("broken", 0.3, "line one\nline two")
    err = capsys.readouterr().err
    assert "line one" in err
    assert "line two" in err


def test_output_task_skipped(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.set_total(1)
    ui.task_skipped("downstream", "upstream")
    err = capsys.readouterr().err
    assert "Skipped" in err
    assert "downstream" in err
    assert "'upstream'" in err


def test_output_summary_success(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.summary(cooked=3, fresh=2, failed=0, skipped=0, elapsed=4.5)
    err = capsys.readouterr().err
    assert "Build finished" in err
    assert "3 cooked" in err
    assert "2 fresh" in err


def test_output_summary_failure(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.summary(cooked=1, fresh=0, failed=2, skipped=1, elapsed=3.0)
    err = capsys.readouterr().err
    assert "Build failed" in err
    assert "2 failed" in err
    assert "1 skipped" in err


def test_output_quiet_suppresses(capsys: pytest.CaptureFixture[str]):
    ui = Output(verbosity=Verbosity.QUIET, color=False)
    ui.set_total(3)
    ui.task_fresh("t1")
    ui.task_cooked("t2", 1.0)
    ui.task_skipped("t3", "t1")
    err = capsys.readouterr().err
    assert err == ""


def test_output_quiet_shows_failures(capsys: pytest.CaptureFixture[str]):
    ui = Output(verbosity=Verbosity.QUIET, color=False)
    ui.set_total(1)
    ui.task_failed("t", 0.5, "boom")
    err = capsys.readouterr().err
    assert "FAILED" in err


def test_output_verbose(capsys: pytest.CaptureFixture[str]):
    ui = Output(verbosity=Verbosity.VERBOSE, color=False)
    ui.verbose("debug info")
    err = capsys.readouterr().err
    assert "debug info" in err


def test_output_verbose_suppressed_in_normal(capsys: pytest.CaptureFixture[str]):
    ui = Output(verbosity=Verbosity.NORMAL, color=False)
    ui.verbose("debug info")
    err = capsys.readouterr().err
    assert err == ""


def test_output_error(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.error("something broke")
    err = capsys.readouterr().err
    assert "error: something broke" in err


def test_output_status(capsys: pytest.CaptureFixture[str]):
    ui = Output(color=False)
    ui.status("hello")
    err = capsys.readouterr().err
    assert "hello" in err
