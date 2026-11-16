"""Unified diffs as the commands show them: a few lines on the terminal, the rest counted."""

from __future__ import annotations

import difflib
import os
import re
import sys
from collections.abc import Mapping
from pathlib import PurePath
from typing import NamedTuple, TextIO

from .color import BOLD, CYAN, GREEN, RED, color_enabled, paint
from .credentials import secret_reason

DIFF_LIMIT = 40


NO_NEWLINE = "\\ No newline at end of file\n"

_HUNK = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


def diff_lines(
    rel: str, actual: str, expected: str, context: int, *, format_name: str | None = None
) -> list[str]:
    """A unified diff whose every line ends in a newline: a last line that lacks one is
    terminated and marked, as git marks it, so it never runs into the next. A file either
    side of which holds a secret is never diffed, on a terminal or in a file: one line
    names it and counts the lines that differ, so no value is printed and no change is
    hidden (ADR 0032)."""
    before = actual.splitlines(keepends=True)
    after = expected.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, before, after)
    groups = list(matcher.get_grouped_opcodes(context))
    if not groups:
        return []
    format_name = format_name or PurePath(rel).suffix[1:]
    reason = secret_reason(actual, format_name) or secret_reason(expected, format_name)
    if reason is not None:
        count = sum(
            max(high - low, stop - start)
            for tag, low, high, start, stop in matcher.get_opcodes()
            if tag != "equal"
        )
        withheld = (
            f"{rel}: {count} line(s) differ; diff not shown because the file holds a secret "
            f"({reason}) — read the file directly\n"
        )
        return [withheld]
    shown = [f"--- {rel} (on disk)\n", f"+++ {rel} (expected)\n"]
    for group in groups:
        first, last = group[0], group[-1]
        shown.append(f"@@ -{_range(first[1], last[2])} +{_range(first[3], last[4])} @@\n")
        for tag, low, high, start, stop in group:
            if tag == "equal":
                shown += [f" {line}" for line in before[low:high]]
                continue
            shown += [f"-{line}" for line in before[low:high]]
            shown += [f"+{line}" for line in after[start:stop]]
    lines: list[str] = []
    for line in shown:
        lines += [line] if line.endswith("\n") else [f"{line}\n", NO_NEWLINE]
    return lines


def withheld(lines: list[str]) -> bool:
    """Whether `diff_lines` named the file instead of diffing it."""
    return bool(lines) and not lines[0].startswith("--- ")


def _range(start: int, stop: int) -> str:
    """A hunk's range as `difflib.unified_diff` writes it."""
    length = stop - start
    if length == 1:
        return f"{start + 1}"
    return f"{start if not length else start + 1},{length}"


class _Place(NamedTuple):
    old: int = 0
    new: int = 0
    header: bool = False


def _body_style(line: str, place: _Place) -> tuple[str | None, _Place]:
    if line.startswith("-"):
        return RED, place._replace(old=place.old - 1)
    if line.startswith("+"):
        return GREEN, place._replace(new=place.new - 1)
    if line.startswith(" "):
        return None, _Place(place.old - 1, place.new - 1)
    return None, place


def _style(line: str, following: str, place: _Place) -> tuple[str | None, _Place]:
    if place.header:
        return BOLD, _Place()
    if place.old or place.new:
        return _body_style(line, place)
    if hunk := _HUNK.match(line):
        return CYAN, _Place(int(hunk[1] or 1), int(hunk[2] or 1))
    if line.startswith("--- ") and following.startswith("+++ "):
        return BOLD, _Place(header=True)
    return None, _Place()


def colored(lines: list[str]) -> list[str]:
    """Each line painted by its place in the diff, so a removed `-- note` is red rather
    than a header, and a line that names a file stays plain."""
    painted: list[str] = []
    place = _Place()
    for line, following in zip(lines, [*lines[1:], ""], strict=True):
        style, place = _style(line, following, place)
        painted.append(line if style is None else paint(line, style))
    return painted


def report_diff(
    lines: list[str], stream: TextIO | None = None, environ: Mapping[str, str] | None = None
) -> bool:
    """Show at most DIFF_LIMIT lines of a diff, on stderr unless told otherwise; True
    when some were left out."""
    stream = stream or sys.stderr
    shown = lines[:DIFF_LIMIT]
    on = color_enabled(stream, os.environ if environ is None else environ)
    stream.writelines(colored(shown) if on else shown)
    if len(lines) <= DIFF_LIMIT:
        return False
    print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=stream)
    return True
