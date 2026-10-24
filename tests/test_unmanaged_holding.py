"""Which directories hold an output, worked out as text (review 3, batch 3.5)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import loadout
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


@pytest.mark.integration
def test_sync_and_check_name_a_retired_skill_s_leftovers_alike(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 5, batch 2.6: sync scanned with the record from before it retired the skill,
    so it named the file inside and check, the folder. The leftover is a .md Pi would load
    from a folder of `.agents/skills` without SKILL.md, so both report it."""
    root = tmp_path / "p"
    for name in ("x", "y"):
        (root / f"loadout/skills/{name}").mkdir(parents=True)
        (root / f"loadout/skills/{name}/SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n{name}\n"
        )
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / ".agents/skills/x/notes.md").write_text("mine\n")
    shutil.rmtree(root / "loadout/skills/x")
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 0
    warned = [line.split(" is ")[0] for line in capsys.readouterr().err.splitlines()]
    assert loadout.main(["check", "--root", str(root)]) == 1
    drifted = [line for line in capsys.readouterr().err.splitlines() if line.startswith("DRIFT")]

    assert (warned, drifted) == (
        ["WARNING: .agents/skills/x"],
        ["DRIFT (unmanaged): .agents/skills/x"],
    )
