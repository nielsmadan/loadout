"""`integrate skill copy`: the bundled skill copied as it is into each harness's user skills
folder, for the conversion init could not do (ADR 0033)."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.skill_copy import copy_command, skill_folders


def _files(folder: Path) -> dict[Path, bytes]:
    return {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


BUNDLED = Path(loadout.__file__).parent / "_skills/loadout"


def test_each_harness_reads_its_own_folder_and_three_share_one(fake_home: Path) -> None:
    assert skill_folders(["claude", "codex", "droid", "opencode", "pi"]) == {
        fake_home / ".claude/skills": ("claude",),
        fake_home / ".agents/skills": ("codex", "opencode", "pi"),
        fake_home / ".factory/skills": ("droid",),
    }


def test_a_relocated_harness_gets_the_skill_where_it_reads_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(tmp_path / "factory"))

    assert list(skill_folders(["claude", "droid"])) == [
        tmp_path / "claude/skills",
        tmp_path / "factory/.factory/skills",
    ]


def test_the_command_names_each_harness_once() -> None:
    assert copy_command(["codex", "pi", "codex"]) == (
        "loadout integrate skill copy --harness codex --harness pi"
    )


def test_copy_installs_the_skill_and_says_how_to_start_it(fake_home: Path, capsys) -> None:
    argv = ["integrate", "skill", "copy", "--harness", "opencode", "--harness", "pi"]

    assert loadout.main(argv) == 0

    target = fake_home / ".agents/skills/loadout"
    assert _files(target) == _files(BUNDLED)
    assert capsys.readouterr().out.splitlines() == [
        f"Copied the loadout skill to {target}",
        "In opencode, ask it to use the loadout skill.",
        "In pi, type /skill:loadout.",
    ]


def test_copy_leaves_an_identical_copy_and_fills_the_missing_folder(
    fake_home: Path, capsys
) -> None:
    assert loadout.main(["integrate", "skill", "copy", "--harness", "claude"]) == 0
    capsys.readouterr()

    assert (
        loadout.main(["integrate", "skill", "copy", "--harness", "claude", "--harness", "pi"]) == 0
    )

    assert capsys.readouterr().out.splitlines()[:2] == [
        f"Already there: {fake_home / '.claude/skills/loadout'}",
        f"Copied the loadout skill to {fake_home / '.agents/skills/loadout'}",
    ]


def test_copy_refuses_a_differing_copy_before_copying_anything(fake_home: Path, capsys) -> None:
    theirs = fake_home / ".factory/skills/loadout/SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("mine\n")

    assert (
        loadout.main(["integrate", "skill", "copy", "--harness", "claude", "--harness", "droid"])
        == 3
    )

    assert (
        f"a different loadout skill is already at {theirs.parent}; nothing was copied"
        in capsys.readouterr().err
    )
    assert theirs.read_text() == "mine\n"
    assert not (fake_home / ".claude/skills/loadout").exists()


def test_copy_requires_a_harness(capsys) -> None:
    assert loadout.main(["integrate", "skill", "copy"]) == 2

    assert "the following arguments are required: --harness" in capsys.readouterr().err
