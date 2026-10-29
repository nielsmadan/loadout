"""Which directories hold an output, worked out as text (review 3, batch 3.5)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import loadout
from loadout.unmanaged import _holding, harness_ignores


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


def _skill_project(root: Path, harnesses: str) -> Path:
    (root / "loadout/skills/x").mkdir(parents=True)
    (root / "loadout/skills/x/SKILL.md").write_text("---\nname: x\ndescription: d\n---\nx\n")
    (root / "loadout/config.toml").write_text(f"harnesses = {harnesses}\n")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    return root


@pytest.mark.integration
def test_a_root_file_in_an_agents_skills_folder_is_loaded_by_no_reader(
    tmp_path: Path, capsys
) -> None:
    """Pi ignores root files in `.agents` folders; Codex and OpenCode ignore a `.txt`."""
    root = _skill_project(tmp_path / "p", '["codex", "opencode"]')
    (root / ".agents/skills/notes.txt").write_text("mine\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "generated files are up to date"


@pytest.mark.integration
@pytest.mark.parametrize(("name", "status"), [("notes.txt", 0), ("notes.md", 1)])
def test_pi_loads_a_root_md_file_in_its_own_skills_folder(
    tmp_path: Path, capsys, name: str, status: int
) -> None:
    root = _skill_project(tmp_path / "p", '["pi"]')
    (root / ".pi/skills" / name).write_text("---\ndescription: d\n---\nmine\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == status


@pytest.mark.parametrize(
    ("folder", "name", "ignored"),
    [
        (".agents/skills", "notes.md", True),
        (".agents/skills", "notes.txt", True),
        (".pi/skills", "notes.txt", True),
        (".pi/skills", "notes.md", False),
    ],
)
def test_pi_reads_root_md_files_only_in_its_own_skills_folders(
    tmp_path: Path, folder: str, name: str, ignored: bool
) -> None:
    entry = tmp_path / folder / name
    entry.parent.mkdir(parents=True)
    entry.write_text("---\ndescription: d\n---\nmine\n")

    assert harness_ignores(entry, "skills", "pi") is ignored
