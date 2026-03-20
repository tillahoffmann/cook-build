from __future__ import annotations

import json
import textwrap
import threading
from collections.abc import Callable, Generator
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path

import pytest

from cook.cli import main
from cook.cli.cmd_ui import _build_graph_data, _find_free_port, _UIHandler
from cook.context import Context
from cook.store.sqlite import SqliteBuildStore
from cook.task import ShellTask


@pytest.fixture
def ui_server() -> Generator[
    Callable[..., tuple[HTTPServer, int, HTTPConnection]],
    None,
    None,
]:
    """Start a UI server, yield a factory. Servers are shut down automatically."""
    servers: list[HTTPServer] = []

    def start(
        ctx: Context, static_dir: Path | None = None
    ) -> tuple[HTTPServer, int, HTTPConnection]:
        port = _find_free_port()
        handler_class = type(
            "Handler",
            (_UIHandler,),
            {
                "ctx": ctx,
                "config_data": {
                    "pattern": None,
                    "project_root": str(ctx.project_root),
                },
                "static_dir": static_dir,
            },
        )
        server = HTTPServer(("127.0.0.1", port), handler_class)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append(server)
        return server, port, HTTPConnection("127.0.0.1", port)

    yield start

    for s in servers:
        s.shutdown()
    # Reset class-level cache to prevent cross-test contamination
    _UIHandler._cache = {}
    _UIHandler._cache_tag = None


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


def test_build_graph_data_marks_failed(project: Path) -> None:
    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="fail", cmd="exit 1", outputs=["out.txt"])
        """,
    )
    # Run to create a failure record
    rc = main(["run", "fail"])
    assert rc == 1

    with Context(project_root=project) as ctx:
        ctx.sh(name="fail", cmd="exit 1", outputs=["out.txt"])
        ctx.validate()
        tasks, _, _ = _build_graph_data(ctx)

    task_dict = {t["name"]: t for t in tasks}
    assert task_dict["fail"].get("failed") is True


def test_build_graph_data_marks_pending(project: Path) -> None:
    import os
    from datetime import datetime, timezone

    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        """,
    )

    with Context(project_root=project) as ctx:
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        ctx.validate()

        with SqliteBuildStore(str(ctx.db_path)) as store:
            store.start_run(
                "slow", "test-session", os.getpid(), datetime.now(timezone.utc)
            )

        tasks, _, _ = _build_graph_data(ctx)

    task_dict = {t["name"]: t for t in tasks}
    assert task_dict["slow"].get("pending") is True


def test_build_graph_data_marks_running(project: Path) -> None:
    import os
    from datetime import datetime, timezone

    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        """,
    )

    with Context(project_root=project) as ctx:
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        ctx.validate()

        with SqliteBuildStore(str(ctx.db_path)) as store:
            run_id = store.start_run(
                "slow", "test-session", os.getpid(), datetime.now(timezone.utc)
            )
            store.update_run_status(run_id, "running")

        tasks, _, _ = _build_graph_data(ctx)

    task_dict = {t["name"]: t for t in tasks}
    assert task_dict["slow"].get("running") is True


def test_task_detail_shows_pending(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
    import os
    from datetime import datetime, timezone

    from cook.cli.util import load_recipe

    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        """,
    )

    with Context(project_root=project) as ctx:
        load_recipe(str(project / "recipe.py"))
        ctx.validate()

        with SqliteBuildStore(str(ctx.db_path)) as store:
            store.start_run(
                "slow", "test-session", os.getpid(), datetime.now(timezone.utc)
            )

        _, _, conn = ui_server(ctx)
        conn.request("GET", "/api/tasks/slow")
        resp = conn.getresponse()
        assert resp.status == 200
        import json

        detail = json.loads(resp.read())
        assert detail.get("pending") is True


def test_task_detail_shows_running(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
    import os
    from datetime import datetime, timezone

    from cook.cli.util import load_recipe

    _write_recipe(
        project,
        """\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="slow", cmd="sleep 10", outputs=["out.txt"])
        """,
    )

    with Context(project_root=project) as ctx:
        load_recipe(str(project / "recipe.py"))
        ctx.validate()

        with SqliteBuildStore(str(ctx.db_path)) as store:
            run_id = store.start_run(
                "slow", "test-session", os.getpid(), datetime.now(timezone.utc)
            )
            store.update_run_status(run_id, "running")

        _, _, conn = ui_server(ctx)
        conn.request("GET", "/api/tasks/slow")
        resp = conn.getresponse()
        assert resp.status == 200
        import json

        detail = json.loads(resp.read())
        assert detail.get("running") is True


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


def test_api_refreshes_when_db_deleted(
    project: Path,
    ui_server,  # type: ignore[no-untyped-def]
) -> None:
    """Deleting the .cook directory should cause the API to return fresh data."""
    import shutil

    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="t", cmd="echo hi > {outfile}", outputs=["{outfile}"])
        """,
    )
    from cook.cli.util import load_recipe

    with Context(project_root=project) as ctx:
        load_recipe(str(project / "recipe.py"))
        ctx.validate()

        # Run the task to create a store with a record
        from cook.cli import main

        rc = main(["run", "t"])
        assert rc == 0

        _, _, conn = ui_server(ctx)

        # First request — should have task with history
        conn.request("GET", "/api/tasks")
        resp = conn.getresponse()
        assert resp.status == 200
        last_modified = resp.getheader("Last-Modified")
        assert last_modified is not None
        tasks_before = json.loads(resp.read())
        assert len(tasks_before) == 1
        assert tasks_before[0]["stale"] is False  # just ran, should be fresh

        # Delete the .cook directory
        cook_dir = project / ".cook"
        if cook_dir.exists():
            shutil.rmtree(cook_dir)

        # Second request WITH If-Modified-Since — must NOT return 304
        # (the db was deleted, so data has changed even though mtime is "older")
        conn.request("GET", "/api/tasks", headers={"If-Modified-Since": last_modified})
        resp = conn.getresponse()
        assert resp.status == 200  # NOT 304
        tasks_after = json.loads(resp.read())
        assert len(tasks_after) == 1
        assert tasks_after[0]["stale"] is True  # no store = stale
        assert tasks_after[0]["reason"] == "never run"


def test_api_tasks_endpoint(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
    outfile = project / "out.txt"
    _write_recipe(
        project,
        f"""\
        from cook import get_context
        ctx = get_context()
        ctx.sh(name="hello", cmd="echo hi > {outfile}", outputs=["{outfile}"])
        """,
    )
    # Run to create the db (needed for 304 tests)
    from cook.cli import main
    from cook.cli.util import load_recipe

    main(["run", "hello"])

    with Context(project_root=project) as ctx:
        load_recipe(str(project / "recipe.py"))
        ctx.validate()

        _, _, conn = ui_server(ctx)

        # Test /api/tasks
        conn.request("GET", "/api/tasks")
        resp = conn.getresponse()
        assert resp.status == 200
        tasks = json.loads(resp.read())
        assert len(tasks) == 1
        assert tasks[0]["name"] == "hello"

        # Test Last-Modified header
        last_modified = resp.getheader("Last-Modified")
        assert last_modified is not None

        # Test If-Modified-Since → 304
        conn.request("GET", "/api/tasks", headers={"If-Modified-Since": last_modified})
        resp = conn.getresponse()
        assert resp.status == 304

        # Test /api/edges with Last-Modified
        conn.request("GET", "/api/edges")
        resp = conn.getresponse()
        assert resp.status == 200
        edges_lm = resp.getheader("Last-Modified")
        assert edges_lm is not None
        edges = json.loads(resp.read())
        assert isinstance(edges, list)

        # Test edges If-Modified-Since → 304
        conn.request("GET", "/api/edges", headers={"If-Modified-Since": edges_lm})
        resp = conn.getresponse()
        assert resp.status == 304

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


def test_static_file_serving(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
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

        _, _, conn = ui_server(ctx, static_dir=static)

        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"cook ui" in resp.read()

        conn.request("GET", "/assets/app.js")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "application/javascript"

        conn.request("GET", "/assets/app.css")
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.getheader("Content-Type") == "text/css"

        conn.request("GET", "/some/unknown/path")
        resp = conn.getresponse()
        assert resp.status == 200
        assert b"cook ui" in resp.read()

        conn.request("GET", "/api/tasks")
        resp = conn.getresponse()
        assert resp.status == 200


def test_static_dir_no_index(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
    static = project / "static"
    static.mkdir()

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="true", outputs=["out.txt"])
        ctx.validate()

        _, _, conn = ui_server(ctx, static_dir=static)

        conn.request("GET", "/")
        resp = conn.getresponse()
        assert resp.status == 404


def test_path_traversal_blocked(project: Path, ui_server) -> None:  # type: ignore[no-untyped-def]
    static = project / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>ok</html>")

    with Context(project_root=project) as ctx:
        ctx.sh(name="t", cmd="true", outputs=["out.txt"])
        ctx.validate()

        _, _, conn = ui_server(ctx, static_dir=static)

        conn.request("GET", "/../../etc/passwd")
        resp = conn.getresponse()
        assert resp.status == 403


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
