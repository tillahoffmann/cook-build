from __future__ import annotations

import argparse

from ..config import Config
from ..context import Context
from ..ui import Output
from .util import match_targets, run_targets


def cmd_exec(args: argparse.Namespace, config: Config, ctx: Context, ui: Output) -> int:
    targets = match_targets(ctx.tasks, args.pattern, config.default, args.regex)
    return run_targets(targets, args, config, ui)
