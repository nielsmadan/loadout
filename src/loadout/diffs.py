"""Unified diffs as the commands show them: a few lines on the terminal, the rest counted."""

from __future__ import annotations

import difflib
import sys
from pathlib import PurePath
from typing import TextIO

from .credentials import redacted_lines

DIFF_LIMIT = 40


NO_NEWLINE = "\\ No newline at end of file\n"


HIDDEN = "  (differs inside a redacted value)"


def diff_lines(rel: str, actual: str, expected: str, context: int) -> list[str]:
    """A unified diff whose every line ends in a newline: a last line that lacks one is
    terminated and marked, as git marks it, so it never runs into the next. Every secret
    either side holds is redacted here, so no diff loadout prints, on a terminal or in a
    file, can repeat one (review 4, batch 1.2). The diff is of the texts as written and
    only shown redacted, so redaction never hides a change: a changed line that redacts
    the same as its pair says so (review 6, batch 1.2)."""
    raw_before = actual.splitlines(keepends=True)
    raw_after = expected.splitlines(keepends=True)
    before, after = redacted_lines(actual, expected, PurePath(rel).suffix[1:])
    groups = list(difflib.SequenceMatcher(None, raw_before, raw_after).get_grouped_opcodes(context))
    if not groups:
        return []
    shown = [f"--- {rel} (on disk)\n", f"+++ {rel} (expected)\n"]
    for group in groups:
        first, last = group[0], group[-1]
        shown.append(f"@@ -{_range(first[1], last[2])} +{_range(first[3], last[4])} @@\n")
        for tag, low, high, start, stop in group:
            if tag == "equal":
                shown += [f" {line}" for line in before[low:high]]
                continue
            removed, added = before[low:high], after[start:stop]
            alike = {
                index for index, line in enumerate(added[: len(removed)]) if line == removed[index]
            }
            shown += [f"-{line}" for line in removed]
            shown += [
                f"+{_annotated(line) if index in alike else line}"
                for index, line in enumerate(added)
            ]
    lines: list[str] = []
    for line in shown:
        lines += [line] if line.endswith("\n") else [f"{line}\n", NO_NEWLINE]
    return lines


def _annotated(line: str) -> str:
    body = line.rstrip("\r\n")
    return f"{body}{HIDDEN}{line[len(body) :]}"


def _range(start: int, stop: int) -> str:
    """A hunk's range as `difflib.unified_diff` writes it."""
    length = stop - start
    if length == 1:
        return f"{start + 1}"
    return f"{start if not length else start + 1},{length}"


def report_diff(lines: list[str], stream: TextIO | None = None) -> bool:
    """Show at most DIFF_LIMIT lines of a diff, on stderr unless told otherwise; True
    when some were left out."""
    stream = stream or sys.stderr
    stream.writelines(lines[:DIFF_LIMIT])
    if len(lines) <= DIFF_LIMIT:
        return False
    print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=stream)
    return True
