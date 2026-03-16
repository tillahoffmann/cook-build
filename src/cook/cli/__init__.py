from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path

from ..config import Config, ConfigError, load_config
from ..context import Context
from ..executor import TaskExecutionError, get_executor
from ..executor.slurm import PollTimeoutError
from ..scheduler import BuildError, TaskOutputError
from ..ui import Output, Verbosity
from .cmd_build import cmd_build
from .cmd_exec import cmd_exec
from .cmd_inspect import cmd_inspect
from .cmd_invalidate import cmd_invalidate
from .cmd_ls import cmd_ls
from .cmd_validate import cmd_validate
from .util import load_recipe


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cook", description="Cook build system")
    parser.add_argument(
        "--version",
        action="version",
        version=f"cook {version('cook-build')}",
    )
    parser.add_argument(
        "-c", "--config", default=None, help="Config file (default: cook.toml)"
    )
    parser.add_argument(
        "-f", "--file", default=None, help="Recipe file (default: from config)"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detailed output"
    )
    parser.add_argument("-q", "--quiet", action="store_true", help="Only show errors")
    parser.add_argument(
        "--color",
        choices=["auto", "always", "never"],
        default="auto",
        help="Color output (default: auto)",
    )
    sub = parser.add_subparsers(dest="command")

    exec_p = sub.add_parser("exec", help="Run tasks matching a pattern")
    exec_p.add_argument("pattern", nargs="*")
    exec_p.add_argument(
        "-n", "--dry-run", action="store_true", help="Show what would run"
    )
    exec_p.add_argument(
        "-k", "--keep-going", action="store_true", help="Continue on failure"
    )
    exec_p.add_argument(
        "-j", "--jobs", type=int, default=None, help="Number of parallel jobs"
    )
    exec_p.add_argument(
        "-x", "--executor", default=None, help="Override executor backend"
    )
    exec_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )
    exec_p.add_argument(
        "-s",
        "--stream",
        action="store_true",
        help="Stream task output to terminal (no capture)",
    )

    build_p = sub.add_parser("build", help="Run tasks that produce matching outputs")
    build_p.add_argument("pattern", nargs="*")
    build_p.add_argument(
        "-n", "--dry-run", action="store_true", help="Show what would run"
    )
    build_p.add_argument(
        "-k", "--keep-going", action="store_true", help="Continue on failure"
    )
    build_p.add_argument(
        "-j", "--jobs", type=int, default=None, help="Number of parallel jobs"
    )
    build_p.add_argument(
        "-x", "--executor", default=None, help="Override executor backend"
    )
    build_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )
    build_p.add_argument(
        "-s",
        "--stream",
        action="store_true",
        help="Stream task output to terminal (no capture)",
    )

    inspect_p = sub.add_parser("inspect", help="Show dependency graph and staleness")
    inspect_p.add_argument("pattern", nargs="*")
    inspect_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )
    inspect_p.add_argument("--json", action="store_true", help="Output as JSON lines")

    invalidate_p = sub.add_parser("invalidate", help="Invalidate stored digests")
    invalidate_p.add_argument("pattern", nargs="*")
    invalidate_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )

    validate_p = sub.add_parser(
        "validate", help="Mark tasks as up-to-date without running them"
    )
    validate_p.add_argument("pattern", nargs="*")
    validate_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )

    ls_p = sub.add_parser("ls", help="List task names")
    ls_p.add_argument("pattern", nargs="*")
    ls_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )
    ls_p.add_argument("--json", action="store_true", help="Output as JSON lines")
    ls_filter = ls_p.add_mutually_exclusive_group()
    ls_filter.add_argument(
        "-s", "--stale", action="store_true", help="Only show stale tasks"
    )
    ls_filter.add_argument(
        "-c", "--current", action="store_true", help="Only show up-to-date tasks"
    )

    return parser


def _make_output(args: argparse.Namespace) -> Output:
    if args.verbose and args.quiet:
        raise ValueError("Cannot use --verbose and --quiet together")
    if args.verbose:
        verbosity = Verbosity.VERBOSE
    elif args.quiet:
        verbosity = Verbosity.QUIET
    else:
        verbosity = Verbosity.NORMAL

    color: bool | None = None
    if args.color == "always":
        color = True
    elif args.color == "never":
        color = False

    return Output(verbosity=verbosity, color=color)


HandlerFn = Callable[[argparse.Namespace, Config, Context, Output], int]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handlers: dict[str, HandlerFn] = {
        "build": cmd_build,
        "exec": cmd_exec,
        "inspect": cmd_inspect,
        "invalidate": cmd_invalidate,
        "validate": cmd_validate,
        "ls": cmd_ls,
    }

    try:
        ui = _make_output(args)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        config = load_config(Path(args.config) if args.config else None)
        if args.file is not None:
            config.recipe = args.file
    except (ConfigError, FileNotFoundError) as e:
        ui.error(str(e))
        return 1

    recipe_path = Path(config.recipe).resolve()
    project_root = recipe_path.parent

    with Context(project_root=project_root) as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            ui.error(f"loading recipe: {e}")
            return 1

        try:
            ctx.validate()
            executor_name = config.executor
            if hasattr(args, "executor") and args.executor is not None:
                executor_name = args.executor
            executor_cls = get_executor(executor_name)
            executor_cls.validate_tasks(ctx.tasks)
            return handlers[args.command](args, config, ctx, ui)
        except (
            ValueError,
            ConfigError,
            BuildError,
            TaskExecutionError,
            PollTimeoutError,
            TaskOutputError,
            FileNotFoundError,
        ) as e:
            ui.error(str(e))
            return 1
