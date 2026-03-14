from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..config import Config, ConfigError, load_config
from ..context import Context
from ..executor import TaskExecutionError
from ..executor.slurm import PollTimeoutError
from ..scheduler import BuildError, TaskOutputError
from .cmd_exec import cmd_exec
from .cmd_inspect import cmd_inspect
from .cmd_invalidate import cmd_invalidate
from .cmd_ls import cmd_ls
from .cmd_validate import cmd_validate
from .util import load_recipe


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cook", description="Cook build system")
    parser.add_argument(
        "-c", "--config", default=None, help="Config file (default: cook.toml)"
    )
    parser.add_argument(
        "-f", "--file", default=None, help="Recipe file (default: from config)"
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

    inspect_p = sub.add_parser("inspect", help="Show dependency graph and staleness")
    inspect_p.add_argument("pattern", nargs="*")
    inspect_p.add_argument(
        "-r", "--re", action="store_true", dest="regex", help="Use regex matching"
    )

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
    ls_filter = ls_p.add_mutually_exclusive_group()
    ls_filter.add_argument(
        "-s", "--stale", action="store_true", help="Only show stale tasks"
    )
    ls_filter.add_argument(
        "-c", "--current", action="store_true", help="Only show up-to-date tasks"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 1

    handlers: dict[str, Callable[[argparse.Namespace, Config, Context], int]] = {
        "exec": cmd_exec,
        "inspect": cmd_inspect,
        "invalidate": cmd_invalidate,
        "validate": cmd_validate,
        "ls": cmd_ls,
    }

    try:
        config = load_config(Path(args.config) if args.config else None)
        if args.file is not None:
            config.recipe = args.file
    except (ConfigError, FileNotFoundError) as e:
        print(f"Error: {e}")
        return 1

    with Context() as ctx:
        try:
            load_recipe(config.recipe)
        except Exception as e:
            print(f"Error loading recipe: {e}")
            return 1

        try:
            ctx.validate()
            return handlers[args.command](args, config, ctx)
        except (
            ValueError,
            ConfigError,
            BuildError,
            TaskExecutionError,
            PollTimeoutError,
            TaskOutputError,
            FileNotFoundError,
        ) as e:
            print(f"Error: {e}")
            return 1
