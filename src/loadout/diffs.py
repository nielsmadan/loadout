"""Unified diffs as the commands show them: a few lines on the terminal, the rest counted."""

from __future__ import annotations

import difflib
import sys
from pathlib import PurePath
from typing import TextIO

from .credentials import redacted_pair

DIFF_LIMIT = 40


NO_NEWLINE = "\\ No newline at end of file\n"


def diff_lines(rel: str, actual: str, expected: str, context: int) -> list[str]:
    """A unified diff whose every line ends in a newline: a last line that lacks one is
    terminated and marked, as git marks it, so it never runs into the next. Every secret
    either side holds is redacted here, so no diff loadout prints, on a terminal or in a
    file, can repeat one (review 4, batch 1.2)."""
    shown = redacted_pair(actual, expected, PurePath(rel).suffix[1:])
    if shown[0] == shown[1] and actual != expected:
        # An empty diff would read as nothing to port (review 5, batch 1.3).
        return [f"{rel}: differs only inside a redacted value\n"]
    lines: list[str] = []
    for line in difflib.unified_diff(
        shown[0].splitlines(keepends=True),
        shown[1].splitlines(keepends=True),
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
