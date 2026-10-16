"""`init project` reports a link project sync would refuse instead of walking it, and never
plans to retire a path outside the harness folders it adopts from (review 3, 1.3)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

SKILL = "---\nname: x\ndescription: d\n---\nX\n"


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    config = root / ".git/config"
    config.write_text(config.read_text() + "[http]\n\textraheader = TOKEN-IN-GIT-CONFIG\n")
    (root / ".claude/skills/x").mkdir(parents=True)
    (root / ".claude/skills/x/SKILL.md").write_text(SKILL)
    return root


def _preview(root: Path, capsys) -> tuple[int, str]:
    code = loadout.main(
        ["init", "project", "--root", str(root), "--harness", "claude", "--dry-run"]
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_a_skill_link_into_the_git_dir_is_reported_not_walked(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path / "p")
    link = root / ".claude/skills/x/g"
    link.symlink_to("../../../.git")

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to {root / '.git'}, inside the repository's .git" in out
    assert "TOKEN-IN-GIT-CONFIG" not in (root / ".claude/skills/x/SKILL.md").read_text()
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_linked_folder_inside_a_harness_tree_is_reported_not_walked(
    tmp_path: Path, capsys
) -> None:
    root = _repo(tmp_path / "p")
    (root / "tools").mkdir()
    (root / "tools/run.sh").write_text("echo run\n")
    link = root / ".claude/skills/x/scripts"
    link.symlink_to("../../../tools", target_is_directory=True)

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to the folder {root / 'tools'}" in out
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_linked_file_inside_a_harness_tree_is_reported(tmp_path: Path, capsys) -> None:
    """Sync never writes through it, so the generated copy could not replace it."""
    root = _repo(tmp_path / "p")
    (root / "tools").mkdir()
    (root / "tools/run.sh").write_text("echo run\n")
    link = root / ".claude/skills/x/run.sh"
    link.symlink_to("../../../tools/run.sh")

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to the file {root / 'tools/run.sh'}" in out
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_preview_refusal_names_the_project_rather_than_scratch(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path / "p")
    (root / "docs").mkdir()
    (root / "docs/rules.md").write_text("Rules.\n")
    (root / "CLAUDE.md").symlink_to("docs/rules.md")

    code, out = _preview(root, capsys)

    assert code == 3
    assert f"loadout: refusing to write {root / 'CLAUDE.md'} -> {root / 'docs/rules.md'}" in out
