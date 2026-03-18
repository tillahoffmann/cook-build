from __future__ import annotations

import json
import textwrap
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from cook.cli import main
from cook.cli.cmd_ui import _build_graph_data, _find_free_port, _UIHandler
from cook.context import Context
from cook.task import ShellTask


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_recipe(project: Path, code: str) -> None:
    (project / "recipe.py").write_text(textwrap.dedent(code))


def test_build_graph_data(project: Path) -> None:
    with Context(project_root=project) as ctx:
        a = ctx.sh(
            name="compile", cmd="gcc -c foo.c", inputs=["foo.c"], outputs=["foo.o"]
        )
        ctx.sh(name="link", cmd="gcc foo.o -o app", inputs=[a], outputs=["app"])
        ctx.validate()

        tasks, edges, detail = _build_graph_data(ctx)

    assert len(tasks) == 2
    names = {t["name"] for t in tasks}
    assert names == {"compile", "link"}

    assert {"from": "compile", "to": "link"} in edges

    assert "compile" in detail
    assert "link" in detail


def test_build_graph_data_with_store(project: Path) -> None:
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
    # Run to create store
    rc = main(["run", "*"])
    assert rc == 0

    with Context(project_root=project) as ctx:
        a = ctx.sh(name="step-a", cmd=f"echo a > {out_a}", outputs=[str(out_a)])
        ctx.sh(name="step-b", cmd=f"echo b > {out_b}", inputs=[a], outputs=[str(out_b)])
        ctx.validate()
        tasks, edges, detail = _build_graph_data(ctx)

    assert len(tasks) == 2
    assert {"from": "step-a", "to": "step-b"} in edges
    stale, reason, record = detail["step-a"]
    assert not stale
    assert record is not None


def test_task_detail_includes_history(project: Path) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo ok > out.txt", outputs=["out.txt"])
        """,
    )
    rc = main(["run", "t"])
    assert rc == 0

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="echo ok > out.txt", outputs=["out.txt"])
        ctx.validate()
        _, _, detail = _build_graph_data(ctx)

    from cook.cli.cmd_ui import _task_detail_dict

    stale, reason, record = detail["t"]
    task = ctx.tasks["t"]
    result = _task_detail_dict(task, stale, reason, record)
    history = result["history"]
    assert isinstance(history, dict)
    assert "last_succeeded" in history


def test_task_detail_no_history(project: Path) -> None:
    from cook.cli.cmd_ui import _task_detail_dict

    task = ShellTask(name="new", cmd="true", outputs=["out.txt"])
    result = _task_detail_dict(task, True, "never run", None)
    assert result["history"] is None
    assert result["reason"] == "never run"


def test_shorten_path_inside_home() -> None:
    from cook.cli.cmd_ui import _shorten_path

    home = Path.home()
    result = _shorten_path(home / "projects" / "my-app")
    assert result == "~/projects/my-app"


def test_shorten_path_outside_home() -> None:
    from cook.cli.cmd_ui import _shorten_path

    result = _shorten_path(Path("/tmp/some/path"))
    assert result == "/tmp/some/path"


def test_find_free_port() -> None:
    port = _find_free_port()
    assert isinstance(port, int)
    assert port > 0


def test_find_free_port_fallback() -> None:
    """Falls back to random port when default is taken."""
    import socket as sock

    from cook.cli.cmd_ui import DEFAULT_PORT

    # Occupy the default port (skip if already taken)
    blocker = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
    try:
        blocker.bind(("127.0.0.1", DEFAULT_PORT))
    except OSError:
        blocker.close()
        pytest.skip("default port already in use")
    try:
        port = _find_free_port()
        assert port != DEFAULT_PORT
        assert port > 0
    finally:
        blocker.close()


def test_api_tasks_endpoint(project: Path) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="hello", cmd="echo hi", outputs=["out.txt"])
        """,
    )
    from http.server import HTTPServer

    from cook.cli.util import load_recipe

    with Context(project_root=project) as ctx:
        load_recipe(str(project / "recipe.py"))
        ctx.validate()
        tasks_api, edges, detail_cache = _build_graph_data(ctx)

        port = _find_free_port()
        handler_class = type(
            "Handler",
            (_UIHandler,),
            {
                "tasks_data": tasks_api,
                "edges_data": edges,
                "detail_cache": detail_cache,
                "task_objects": ctx.tasks,
                "config_data": {"pattern": None, "project_root": str(project)},
                "static_dir": None,
            },
        )
        server = HTTPServer(("127.0.0.1", port), handler_class)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            conn = HTTPConnection("127.0.0.1", port)

            # Test /api/tasks
            conn.request("GET", "/api/tasks")
            resp = conn.getresponse()
            assert resp.status == 200
            tasks = json.loads(resp.read())
            assert len(tasks) == 1
            assert tasks[0]["name"] == "hello"

            # Test /api/edges
            conn.request("GET", "/api/edges")
            resp = conn.getresponse()
            assert resp.status == 200
            edges = json.loads(resp.read())
            assert isinstance(edges, list)

            # Test /api/tasks/:name
            conn.request("GET", "/api/tasks/hello")
            resp = conn.getresponse()
            assert resp.status == 200
            detail = json.loads(resp.read())
            assert detail["name"] == "hello"
            assert "history" in detail

            # Test /api/tasks/:name not found
            conn.request("GET", "/api/tasks/nonexistent")
            resp = conn.getresponse()
            assert resp.status == 404

            # Test /api/config
            conn.request("GET", "/api/config")
            resp = conn.getresponse()
            assert resp.status == 200
            config = json.loads(resp.read())
            assert config["pattern"] is None

            # Test missing static dir
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 404
        finally:
            server.shutdown()


def test_static_file_serving(project: Path) -> None:
    from http.server import HTTPServer

    # Create a fake static dir
    static = project / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>cook ui</html>")
    assets = static / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("console.log('hi')")
    (assets / "app.css").write_text("body{}")

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="true", outputs=["out.txt"])
        ctx.validate()
        tasks_api, edges, detail_cache = _build_graph_data(ctx)

        port = _find_free_port()
        handler_class = type(
            "Handler",
            (_UIHandler,),
            {
                "tasks_data": tasks_api,
                "edges_data": edges,
                "detail_cache": detail_cache,
                "task_objects": ctx.tasks,
                "config_data": {"pattern": None, "project_root": str(project)},
                "static_dir": static,
            },
        )
        server = HTTPServer(("127.0.0.1", port), handler_class)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            conn = HTTPConnection("127.0.0.1", port)

            # Test index.html
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
            assert b"cook ui" in resp.read()

            # Test JS asset
            conn.request("GET", "/assets/app.js")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "application/javascript"

            # Test CSS asset
            conn.request("GET", "/assets/app.css")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.getheader("Content-Type") == "text/css"

            # Test SPA fallback (unknown path serves index.html)
            conn.request("GET", "/some/unknown/path")
            resp = conn.getresponse()
            assert resp.status == 200
            assert b"cook ui" in resp.read()

            # API still works alongside static
            conn.request("GET", "/api/tasks")
            resp = conn.getresponse()
            assert resp.status == 200
        finally:
            server.shutdown()


def test_static_dir_no_index(project: Path) -> None:
    from http.server import HTTPServer

    # Static dir exists but has no index.html
    static = project / "static"
    static.mkdir()

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="true", outputs=["out.txt"])
        ctx.validate()
        tasks_api, edges, detail_cache = _build_graph_data(ctx)

        port = _find_free_port()
        handler_class = type(
            "Handler",
            (_UIHandler,),
            {
                "tasks_data": tasks_api,
                "edges_data": edges,
                "detail_cache": detail_cache,
                "task_objects": ctx.tasks,
                "config_data": {"pattern": None, "project_root": str(project)},
                "static_dir": static,
            },
        )
        server = HTTPServer(("127.0.0.1", port), handler_class)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 404
        finally:
            server.shutdown()


def test_path_traversal_blocked(project: Path) -> None:
    from http.server import HTTPServer

    static = project / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>")

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="true", outputs=["out.txt"])
        ctx.validate()
        tasks_api, edges, detail_cache = _build_graph_data(ctx)

        port = _find_free_port()
        handler_class = type(
            "Handler",
            (_UIHandler,),
            {
                "tasks_data": tasks_api,
                "edges_data": edges,
                "detail_cache": detail_cache,
                "task_objects": ctx.tasks,
                "config_data": {"pattern": None, "project_root": str(project)},
                "static_dir": static,
            },
        )
        server = HTTPServer(("127.0.0.1", port), handler_class)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        try:
            conn = HTTPConnection("127.0.0.1", port)
            conn.request("GET", "/../../etc/passwd")
            resp = conn.getresponse()
            assert resp.status == 403
        finally:
            server.shutdown()


def test_guess_content_type() -> None:
    from cook.cli.cmd_ui import _guess_content_type

    assert _guess_content_type(Path("foo.html")) == "text/html"
    assert _guess_content_type(Path("foo.js")) == "application/javascript"
    assert _guess_content_type(Path("foo.css")) == "text/css"
    assert _guess_content_type(Path("foo.json")) == "application/json"
    assert _guess_content_type(Path("foo.svg")) == "image/svg+xml"
    assert _guess_content_type(Path("foo.png")) == "image/png"
    assert _guess_content_type(Path("foo.ico")) == "image/x-icon"
    assert _guess_content_type(Path("foo.woff2")) == "application/octet-stream"


def test_task_to_api_dict_no_cmd() -> None:
    from cook.cli.cmd_ui import _task_to_api_dict
    from cook.task import Task

    task = Task(name="plain", outputs=["out.txt"])
    result = _task_to_api_dict(task, True, "never run")
    assert result["name"] == "plain"
    assert result["type"] == "Task"
    assert "cmd" not in result


def test_task_detail_with_all_history_fields(project: Path) -> None:
    from datetime import datetime, timezone

    from cook.cli.cmd_ui import _task_detail_dict
    from cook.store import TaskRecord

    task = ShellTask(name="t", cmd="true", outputs=["out.txt"])
    record = TaskRecord(
        task_id="t",
        digest="abc",
        last_started=datetime(2026, 1, 1, tzinfo=timezone.utc),
        last_succeeded=datetime(2026, 1, 1, 0, 0, 5, tzinfo=timezone.utc),
        last_failed=datetime(2025, 12, 31, tzinfo=timezone.utc),
        error="old error",
    )
    result = _task_detail_dict(task, False, None, record)
    history = result["history"]
    assert isinstance(history, dict)
    assert history["last_started"] is not None
    assert history["last_succeeded"] is not None
    assert history["last_failed"] is not None
    assert history["error"] == "old error"
    assert history["duration"] is not None
