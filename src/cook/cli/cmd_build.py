from __future__ import annotations

import argparse

from ..config import Config
from ..context import Context
from ..ui import Output
from .util import match_outputs, run_targets


def cmd_build(
    args: argparse.Namespace, config: Config, ctx: Context, ui: Output
) -> int:
    targets = match_outputs(ctx.tasks, args.pattern, args.regex)
    return run_targets(targets, args, config, ctx, ui)
