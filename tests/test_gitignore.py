from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.gitignore import append_lines, escape, remove_lines


def test_glob_characters_and_spaces_are_escaped() -> None:
    assert escape("/.claude/skills/x[1] *?\\/") == "/.claude/skills/x\\[1\\]\\ \\*\\?\\\\/"


def test_a_line_break_is_refused() -> None:
    with pytest.raises(LoadoutError, match="line break"):
        escape("/a\nb")


def test_append_adds_each_missing_line_once(tmp_path: Path) -> None:
    assert append_lines(tmp_path, ["/a", "/b", "/a"]) == 2
    assert append_lines(tmp_path, ["/b", "/c"]) == 1

    assert (tmp_path / ".gitignore").read_text() == "/a\n/b\n/c\n"


def test_append_keeps_existing_content_as_it_was(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# mine\nnode_modules")

    assert append_lines(tmp_path, ["/a"]) == 1

    assert (tmp_path / ".gitignore").read_text() == "# mine\nnode_modules\n/a\n"


def test_remove_drops_exactly_the_named_lines(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# mine\n/a\nnode_modules\n/b\n")

    assert remove_lines(tmp_path, ["/a", "/b", "/absent"]) == 2

    assert (tmp_path / ".gitignore").read_text() == "# mine\nnode_modules\n"
