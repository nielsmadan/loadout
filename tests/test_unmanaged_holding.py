"""Which directories hold an output, worked out as text (review 3, batch 3.5)."""

from __future__ import annotations

from pathlib import Path

from loadout.unmanaged import _holding


def test_every_directory_above_an_output_holds_one() -> None:
    paths = [Path("/h/.claude/skills/a/SKILL.md"), Path("/h/.claude/skills/b/run.sh")]

    assert _holding(paths) == {
        "/h",
        "/h/.claude",
        "/h/.claude/skills",
        "/h/.claude/skills/a",
        "/h/.claude/skills/b",
    }


def test_a_path_whose_parent_is_already_found_still_adds_its_own_folder() -> None:
    """Walking up stops at the first ancestor already found, never before its own."""
    paths = [Path("/h/a/x"), Path("/h/a/b/c/y")]

    assert _holding(paths) == {"/h", "/h/a", "/h/a/b", "/h/a/b/c"}
