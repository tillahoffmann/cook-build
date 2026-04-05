from __future__ import annotations

import os
import sys
from enum import IntEnum


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(seconds)
    if total < 3600:
        return f"{total // 60}m {total % 60}s"
    return f"{total // 3600}h {total % 3600 // 60}m"


class Verbosity(IntEnum):
    QUIET = 0
    NORMAL = 1
    VERBOSE = 2


def _should_color(override: bool | None) -> bool:
    if override is not None:
        return override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CLICOLOR_FORCE"):
        return True
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_BOLD_RED = "\033[1;31m"
_BOLD_GREEN = "\033[1;32m"


class Style:
    def __init__(self, enabled: bool) -> None:
        self._enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        if not self._enabled:
            return text
        return f"{code}{text}{_RESET}"

    def green(self, text: str) -> str:
        return self._wrap(_BOLD_GREEN, text)

    def red(self, text: str) -> str:
        return self._wrap(_BOLD_RED, text)

    def yellow(self, text: str) -> str:
        return self._wrap(_YELLOW, text)

    def cyan(self, text: str) -> str:
        return self._wrap(_CYAN, text)

    def dim(self, text: str) -> str:
        return self._wrap(_DIM, text)

    def bold(self, text: str) -> str:
        return self._wrap(_BOLD, text)


class Output:
    def __init__(
        self,
        verbosity: Verbosity = Verbosity.NORMAL,
        color: bool | None = None,
    ) -> None:
        self.verbosity = verbosity
        self.style = Style(_should_color(color))
        self._total = 0
        self._done = 0

    def set_total(self, total: int) -> None:
        self._total = total

    def _counter(self) -> str:
        self._done += 1
        width = len(str(self._total))
        return f"[{self._done:>{width}}/{self._total}]"

    def task_fresh(self, name: str) -> None:
        if self.verbosity < Verbosity.NORMAL:
            return
        counter = self._counter()
        action = self.style.cyan("Fresh")
        print(f"{counter} {action}   {name}", file=sys.stderr)

    def task_cooking(self, name: str) -> None:
        if self.verbosity < Verbosity.NORMAL:
            return
        width = len(str(self._total))
        pad = " " * (2 * width + 3)  # matches "[N/M]" width
        action = self.style.yellow("Cooking")
        print(f"{pad} {action} {name}", file=sys.stderr)

    def task_cooked(self, name: str, elapsed: float) -> None:
        if self.verbosity < Verbosity.NORMAL:
            return
        counter = self._counter()
        action = self.style.green("Cooked")
        timing = self.style.dim(f"({format_duration(elapsed)})")
        print(f"{counter} {action}  {name} {timing}", file=sys.stderr)

    def task_failed(self, name: str, elapsed: float, message: str) -> None:
        # Always show failures, even in quiet mode
        counter = self._counter()
        action = self.style.red("FAILED")
        timing = self.style.dim(f"({format_duration(elapsed)})")
        print(f"{counter} {action}  {name} {timing}", file=sys.stderr)
        for line in message.splitlines():
            print(f"         {line}", file=sys.stderr)

    def task_skipped(self, name: str, failed_dep: str) -> None:
        if self.verbosity < Verbosity.NORMAL:
            return
        counter = self._counter()
        action = self.style.yellow("Skipped")
        detail = self.style.dim(f"(dep {failed_dep!r} failed)")
        print(f"{counter} {action} {name} {detail}", file=sys.stderr)

    def summary(
        self,
        cooked: int,
        fresh: int,
        failed: int,
        skipped: int,
        elapsed: float,
    ) -> None:
        parts: list[str] = []
        if cooked:
            parts.append(self.style.green(f"{cooked} cooked"))
        if fresh:
            parts.append(self.style.cyan(f"{fresh} fresh"))
        if skipped:
            parts.append(self.style.yellow(f"{skipped} skipped"))
        if failed:
            parts.append(self.style.red(f"{failed} failed"))

        summary = ", ".join(parts)
        timing = self.style.dim(f"in {format_duration(elapsed)}")

        if failed:
            status = self.style.red("Build failed")
        else:
            status = self.style.green("Build finished")

        print(f"\n{status}: {summary} {timing}", file=sys.stderr)

    def error(self, msg: str) -> None:
        label = self.style.red("error")
        print(f"{label}: {msg}", file=sys.stderr)

    def status(self, msg: str) -> None:
        if self.verbosity >= Verbosity.NORMAL:
            print(msg, file=sys.stderr)

    def verbose(self, msg: str) -> None:
        if self.verbosity >= Verbosity.VERBOSE:
            print(msg, file=sys.stderr)
