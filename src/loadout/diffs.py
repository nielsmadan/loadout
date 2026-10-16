"""Unified diffs as the commands show them: a few lines on the terminal, the rest counted."""

from __future__ import annotations

import difflib
import sys
from typing import TextIO

DIFF_LIMIT = 40


NO_NEWLINE = "\\ No newline at end of file\n"


def diff_lines(rel: str, actual: str, expected: str, context: int) -> list[str]:
    """A unified diff whose every line ends in a newline: a last line that lacks one is
    terminated and marked, as git marks it, so it never runs into the next."""
    lines: list[str] = []
    for line in difflib.unified_diff(
        actual.splitlines(keepends=True),
        expected.splitlines(keepends=True),
        fromfile=f"{rel} (on disk)",
        tofile=f"{rel} (expected)",
        n=context,
    ):
        lines += [line] if line.endswith("\n") else [f"{line}\n", NO_NEWLINE]
    return lines


def report_diff(lines: list[str], stream: TextIO | None = None) -> bool:
    """Show at most DIFF_LIMIT lines of a diff, on stderr unless told otherwise; True
    when some were left out."""
    stream = stream or sys.stderr
    stream.writelines(lines[:DIFF_LIMIT])
    if len(lines) <= DIFF_LIMIT:
        return False
    print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=stream)
    return True
