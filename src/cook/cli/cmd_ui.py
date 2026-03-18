from __future__ import annotations

import argparse
import json
import socket
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

from ..config import Config
from ..context import Context
from ..scheduler import is_stale, staleness_reason
from ..store import FileDigestCache, TaskRecord
from ..store.sqlite import SqliteBuildStore
from ..task import ShellTask, Task
from ..transform import collect_transitive
from ..ui import Output


def _task_to_api_dict(task: Task, stale: bool, reason: str | None) -> dict[str, object]:
    obj: dict[str, object] = {
        "name": task.name,
        "type": type(task).__name__,
        "stale": stale,
        "reason": reason,
        "deps": [d.name for d in task.task_deps],
        "inputs": [str(f) for f in task.file_inputs],
        "outputs": [str(o) for o in task.outputs],
        "extra": task.extra,
    }
    if isinstance(task, ShellTask) and task.cmd:
        obj["cmd"] = task.cmd
    return obj


def _task_detail_dict(
    task: Task, stale: bool, reason: str | None, record: TaskRecord | None
) -> dict[str, object]:
    obj = _task_to_api_dict(task, stale, reason)
    if record is not None:
        history: dict[str, object] = {}
        if record.last_started:
            history["last_started"] = record.last_started.isoformat()
        if record.last_succeeded:
            history["last_succeeded"] = record.last_succeeded.isoformat()
        if record.last_failed:
            history["last_failed"] = record.last_failed.isoformat()
        if record.duration is not None:
            history["duration"] = round(record.duration, 3)
        if record.error:
            history["error"] = record.error
        obj["history"] = history if history else None
    else:
        obj["history"] = None
    return obj


def _build_graph_data(
    ctx: Context,
) -> tuple[
    list[dict[str, object]],
    list[dict[str, str]],
    dict[str, tuple[bool, str | None, TaskRecord | None]],
]:
    """Build tasks list, edges, and per-task detail data."""
    all_tasks = list(ctx.tasks.values())
    all_transitive = collect_transitive(all_tasks)

    tasks_api: list[dict[str, object]] = []
    edges: list[dict[str, str]] = []
    detail_cache: dict[str, tuple[bool, str | None, TaskRecord | None]] = {}

    db_path = ctx.db_path
    if db_path.exists():
        cache = FileDigestCache()
        with SqliteBuildStore(str(db_path)) as store:
            for task in all_transitive:
                stale = is_stale(task, store, cache, project_root=ctx.project_root)
                reason = staleness_reason(task, store, cache, ctx.project_root)
                record = store.get(task.task_id)
                tasks_api.append(_task_to_api_dict(task, stale, reason))
                detail_cache[task.name] = (stale, reason, record)
                for dep in task.task_deps:
                    edges.append({"from": dep.name, "to": task.name})
    else:
        for task in all_transitive:
            reason = "never run" if task.outputs else "always-run (no outputs)"
            tasks_api.append(_task_to_api_dict(task, True, reason))
            detail_cache[task.name] = (True, reason, None)
            for dep in task.task_deps:
                edges.append({"from": dep.name, "to": task.name})

    return tasks_api, edges, detail_cache


def _shorten_path(path: Path) -> str:
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


DEFAULT_PORT = 4200


def _find_free_port() -> int:
    """Try the default port, fall back to a random one."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", DEFAULT_PORT))
            return DEFAULT_PORT  # pragma: no cover
    except OSError:
        pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class _UIHandler(SimpleHTTPRequestHandler):
    """HTTP handler serving API endpoints and static files."""

    tasks_data: list[dict[str, object]]
    edges_data: list[dict[str, str]]
    detail_cache: dict[str, tuple[bool, str | None, TaskRecord | None]]
    task_objects: dict[str, Task]
    config_data: dict[str, object]
    static_dir: Path | None

    def do_GET(self) -> None:
        if self.path == "/api/tasks":
            self._json_response(self.tasks_data)
        elif self.path == "/api/edges":
            self._json_response(self.edges_data)
        elif self.path.startswith("/api/tasks/"):
            raw_name = self.path[len("/api/tasks/") :]
            name = urllib.parse.unquote(raw_name)
            self._handle_task_detail(name)
        elif self.path == "/api/config":
            self._json_response(self.config_data)
        elif self.static_dir is not None:
            self._serve_static()
        else:
            self._json_response(
                {"error": "UI not built. Run from the dev server."}, 404
            )

    def _json_response(self, data: object, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_task_detail(self, name: str) -> None:
        task = self.task_objects.get(name)
        if task is None:
            self._json_response({"error": f"Task {name!r} not found"}, 404)
            return
        stale, reason, record = self.detail_cache[name]
        self._json_response(_task_detail_dict(task, stale, reason, record))

    def _serve_static(self) -> None:
        assert self.static_dir is not None
        path = self.path.split("?")[0].lstrip("/")
        if not path or path == "index.html":
            file_path = self.static_dir / "index.html"
        else:
            file_path = (self.static_dir / path).resolve()
            # Prevent path traversal
            if not file_path.is_relative_to(self.static_dir.resolve()):
                self.send_error(403)
                return

        if not file_path.exists() or not file_path.is_file():
            # SPA fallback: serve index.html for unrecognized paths
            file_path = self.static_dir / "index.html"

        if not file_path.exists():
            self.send_error(404)
            return

        content = file_path.read_bytes()
        self.send_response(200)
        content_type = _guess_content_type(file_path)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        pass


def _guess_content_type(path: Path) -> str:
    suffixes = {
        ".html": "text/html",
        ".js": "application/javascript",
        ".css": "text/css",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }
    return suffixes.get(path.suffix, "application/octet-stream")


def cmd_ui(  # pragma: no cover
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    tasks_api, edges, detail_cache = _build_graph_data(ctx)

    # Find static directory
    static_dir: Path | None = None
    pkg_static = Path(__file__).parent.parent / "static"
    if pkg_static.is_dir() and (pkg_static / "index.html").exists():
        static_dir = pkg_static

    port = args.port if args.port is not None else _find_free_port()
    pattern = " ".join(args.pattern) if args.pattern else None

    handler_class = type(
        "Handler",
        (_UIHandler,),
        {
            "tasks_data": tasks_api,
            "edges_data": edges,
            "detail_cache": detail_cache,
            "task_objects": ctx.tasks,
            "config_data": {
                "pattern": pattern,
                "project_root": _shorten_path(ctx.project_root),
            },
            "static_dir": static_dir,
        },
    )

    server = HTTPServer(("127.0.0.1", port), handler_class)
    url = f"http://127.0.0.1:{port}"

    if static_dir:
        ui.status(f"Cook UI available at {url}. Press ENTER to exit.")
    else:
        ui.status(f"Cook API available at {url}/api/tasks. Press ENTER to exit.")
        ui.status("UI not built. Start the dev server: cd ui && npm run dev")

    if not args.no_browser and static_dir:
        webbrowser.open(url)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        server.shutdown()
        server.server_close()

    return 0
