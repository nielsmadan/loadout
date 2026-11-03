"""A text original that is not UTF-8 is refused by name, the way a native document is.

Init used to read it only during the isolated reconstruction, so the refusal was
`reconstruction-failed` with a list of internal frames, naming the planned source folder.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

NOT_UTF8 = b"# P \xff\xfe bad\n"
SKILL = "---\nname: s\ndescription: d\n---\nx\n"
NOT_UTF8_ISSUE = "Text document is not UTF-8; re-encode it as UTF-8, then run init again."


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    (root / ".claude/skills/s").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# P\n")
    (root / ".claude/skills/s/SKILL.md").write_text(SKILL)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _issues(capsys) -> list[tuple[str, list[str], str]]:
    preview = json.loads(capsys.readouterr().out)
    return [(i["code"], i["paths"], i["message"]) for i in preview["issues"]]


@pytest.mark.parametrize(("relative", "harness"), [("CLAUDE.md", "claude"), ("AGENTS.md", "codex")])
def test_a_project_instruction_document_that_is_not_utf8_is_named(
    tmp_path: Path, capsys, relative: str, harness: str
) -> None:
    root = _project(tmp_path)
    original = root / relative
    original.write_bytes(NOT_UTF8)
    argv = ["init", "project", "--root", str(root), "--harness", harness, "--dry-run", "--json"]

    assert loadout.main(argv) == 2
    assert _issues(capsys) == [("invalid-native-document", [str(original)], NOT_UTF8_ISSUE)]


def test_a_global_instruction_document_that_is_not_utf8_is_named(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    original = fake_home / ".claude/CLAUDE.md"
    original.parent.mkdir()
    original.write_bytes(NOT_UTF8)
    source = tmp_path / "source"
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    argv = ["init", "global", str(source), "--harness", "claude", "--dry-run", "--json"]

    assert loadout.main(argv) == 2
    assert _issues(capsys) == [("invalid-native-document", [str(original)], NOT_UTF8_ISSUE)]


def test_a_skill_that_is_not_utf8_is_still_copied(tmp_path: Path, capsys) -> None:
    """A skill is copied byte for byte, so its encoding is the author's business."""
    root = _project(tmp_path)
    (root / ".claude/skills/s/SKILL.md").write_bytes(SKILL.encode() + b"\xff\n")
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]

    assert loadout.main(argv) == 0
    assert (root / "loadout/skills/s/SKILL.md").read_bytes() == SKILL.encode() + b"\xff\n"
