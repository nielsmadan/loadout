"""Init leaves a dot entry in a skills folder where it is, as sync does.

Codex 0.166.0 writes its own skills to `~/.codex/skills/.system` on first start, and init read
that folder as a skill without SKILL.md, so `init global --harness codex` refused on any machine
where Codex had run.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

SYSTEM = "---\nname: imagegen\ndescription: Codex's own\n---\nbuilt in\n"
MINE = "---\nname: mine\ndescription: d\n---\nmine\n"


def _skill(folder: Path, text: str) -> None:
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text)


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "agent-config"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


@pytest.mark.parametrize(
    ("harness", "skills"),
    [
        ("codex", ".codex/skills"),
        ("claude", ".claude/skills"),
        ("droid", ".factory/skills"),
        ("opencode", ".config/opencode/skills"),
        ("pi", ".pi/agent/skills"),
    ],
)
def test_init_global_adopts_the_skill_beside_a_dot_folder_and_leaves_it(
    source: Path, fake_home: Path, capsys, harness: str, skills: str
) -> None:
    folder = fake_home / skills
    _skill(folder / ".system/imagegen", SYSTEM)
    _skill(folder / "mine", MINE)

    argv = ["init", "global", str(source), "--harness", harness, "--yes"]
    assert loadout.main(argv) == 0, capsys.readouterr().err
    assert (source / "loadout/skills/mine/SKILL.md").read_text() == MINE
    assert sorted(p.name for p in (source / "loadout/skills").iterdir()) == ["mine"]
    assert (folder / ".system/imagegen/SKILL.md").read_text() == SYSTEM

    assert loadout.main(["sync", "--global"]) == 0, capsys.readouterr().err
    assert loadout.main(["check", "--global"]) == 0, capsys.readouterr().out
    assert (folder / ".system/imagegen/SKILL.md").read_text() == SYSTEM


@pytest.mark.parametrize(
    ("harness", "skills"), [("claude", ".claude/skills"), ("codex", ".agents/skills")]
)
def test_init_project_leaves_a_dot_folder_in_a_skills_folder(
    tmp_path: Path, fake_home: Path, capsys, harness: str, skills: str
) -> None:
    root = tmp_path / "p"
    folder = root / skills
    _skill(folder / ".cache/x", SYSTEM)
    _skill(folder / "mine", MINE)
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    argv = ["init", "project", "--root", str(root), "--harness", harness, "--yes"]
    assert loadout.main(argv) == 0, capsys.readouterr().err
    assert sorted(p.name for p in (root / "loadout/skills").iterdir()) == ["mine"]
    assert loadout.main(["sync", "--root", str(root)]) == 0, capsys.readouterr().err
    assert loadout.main(["check", "--root", str(root)]) == 0, capsys.readouterr().out
    assert (folder / ".cache/x/SKILL.md").read_text() == SYSTEM


def test_a_linked_skills_folder_keeps_reading_its_dot_folder_after_init(
    tmp_path: Path, source: Path, fake_home: Path, capsys
) -> None:
    kept = tmp_path / "dotfiles/codex-skills"
    _skill(kept / ".system/imagegen", SYSTEM)
    _skill(kept / "mine", MINE)
    folder = fake_home / ".codex/skills"
    folder.parent.mkdir()
    folder.symlink_to(kept)

    argv = ["init", "global", str(source), "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 0, capsys.readouterr().err
    assert loadout.main(["sync", "--global"]) == 0, capsys.readouterr().err
    assert loadout.main(["check", "--global"]) == 0, capsys.readouterr().out

    assert (folder / ".system").readlink() == kept / ".system"
    assert (folder / ".system/imagegen/SKILL.md").read_text() == SYSTEM
    assert (kept / ".system/imagegen/SKILL.md").read_text() == SYSTEM
