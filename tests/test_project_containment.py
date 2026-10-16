"""Project-scope sync never writes outside the project, whatever a committed symlink
points at (review 1.5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import loadout
from loadout.errors import LoadoutError
from loadout.templates import remove_vendored

pytestmark = pytest.mark.integration

SKILL = "---\nname: pwn\ndescription: p\n---\nRun it.\n"


def _snapshot(root: Path) -> dict[str, object]:
    return {
        str(Path(directory, name).relative_to(root)): Path(directory, name).lstat().st_size
        for directory, directories, files in os.walk(root)
        for name in (*directories, *files)
    }


def _project(root: Path, home: Path) -> Path:
    (root / "loadout/skills/pwn").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude", "codex"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\nallow = ["curl", "rm"]\n')
    (root / "loadout/skills/pwn/SKILL.md").write_text(SKILL)
    for harness in (".claude", ".codex"):
        (home / harness).mkdir(exist_ok=True)
        (root / harness).symlink_to(os.path.relpath(home / harness, root))
    return root


def test_sync_refuses_a_committed_link_out_of_the_project(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _project(tmp_path / "p", fake_home)
    before = _snapshot(fake_home)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    err = capsys.readouterr().err
    assert f"{root / '.claude'}" in err and "outside the project" in err
    assert _snapshot(fake_home) == before


def _plain_project(root: Path) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    return root


def test_sync_refuses_a_gitignore_that_is_a_symlink(tmp_path: Path, capsys) -> None:
    """Review 1.6: appending ignore lines through a link writes into whatever it names."""
    root = _plain_project(tmp_path / "p")
    keys = tmp_path / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAA mine\n")
    (root / ".gitignore").symlink_to(keys)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert ".gitignore" in capsys.readouterr().err
    assert keys.read_text() == "ssh-ed25519 AAAA mine\n"
    assert not (root / ".claude/settings.json").exists()


@pytest.mark.parametrize("name", ["x\nssh-ed25519 AAAA attacker", "bad\rname", "tab\tname"])
def test_a_skill_name_with_a_control_character_is_refused(
    tmp_path: Path, name: str, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/skills" / name).mkdir(parents=True)
    (root / "loadout/skills" / name / "SKILL.md").write_text(SKILL)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "control character" in capsys.readouterr().err
    assert not (root / ".gitignore").exists()


def test_an_agent_name_with_a_control_character_is_refused(tmp_path: Path, capsys) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/agents").mkdir()
    (root / "loadout/agents/bad\nname.md").write_text("---\ndescription: d\n---\nGo.\n")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "control character" in capsys.readouterr().err


def test_a_skill_file_linking_out_of_the_project_is_refused(tmp_path: Path, capsys) -> None:
    """Review 1.7: a supporting file linked to a secret would be copied into the repo."""
    root = _plain_project(tmp_path / "p")
    secret = tmp_path / "id_ed25519"
    secret.write_text("PRIVATE\n")
    (root / "loadout/skills/s").mkdir(parents=True)
    (root / "loadout/skills/s/SKILL.md").write_text(SKILL)
    (root / "loadout/skills/s/notes.txt").symlink_to(secret)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "notes.txt" in capsys.readouterr().err
    assert not (root / ".claude/skills/s").exists()


def test_a_settings_source_linking_out_of_the_project_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')
    (root / "loadout/settings").mkdir()
    (root / "loadout/settings/claude.json").symlink_to(fake_home / ".claude/settings.json")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "claude.json" in capsys.readouterr().err
    assert not (root / ".claude/settings.json").exists()


def test_removing_a_vendored_template_never_follows_a_link_out(tmp_path: Path) -> None:
    root = _plain_project(tmp_path / "p")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "frontend").mkdir(parents=True)
    (elsewhere / "frontend/instructions.md").write_text("keep\n")
    (root / "loadout/templates").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(LoadoutError, match="symlink"):
        remove_vendored(root, "frontend")

    assert (elsewhere / "frontend/instructions.md").read_text() == "keep\n"


def test_a_linked_directory_inside_the_project_is_followed_to_what_it_holds(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """A linked skill folder lands inside the project, but a file in it links out."""
    root = _plain_project(tmp_path / "src/proj")
    secret = fake_home / ".ssh/id_ed25519"
    secret.parent.mkdir()
    secret.write_text("PRIVATE\n")
    (root / "docs/x").mkdir(parents=True)
    (root / "docs/x/SKILL.md").write_text(SKILL)
    (root / "docs/x/notes.txt").symlink_to(secret)
    (root / "loadout/skills").mkdir()
    (root / "loadout/skills/x").symlink_to("../../docs/x", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "notes.txt" in capsys.readouterr().err
    assert not (root / ".claude/skills/x").exists()


def test_a_linked_settings_folder_is_followed_to_what_it_holds(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "src/proj")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')
    (root / "cfg").mkdir()
    (root / "cfg/claude.json").symlink_to(fake_home / ".claude/settings.json")
    (root / "loadout/settings").symlink_to("../cfg", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "claude.json" in capsys.readouterr().err
    assert not (root / ".claude/settings.json").exists()


def test_a_skill_with_a_local_virtualenv_still_syncs(tmp_path: Path) -> None:
    """Excluded folders are never read, so a link inside one is not refused."""
    root = _plain_project(tmp_path / "p")
    skill = root / "loadout/skills/s"
    (skill / ".venv/bin").mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL)
    (skill / ".venv/bin/python").symlink_to("/usr/bin/python3")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (root / ".claude/skills/s/SKILL.md").is_file()


def test_sync_refuses_a_link_out_before_showing_any_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The guard must not print a diff of a file outside the project (review C)."""
    root = _project(tmp_path / "p", fake_home)
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')

    assert loadout.main(["sync", "--root", str(root)]) == 3

    captured = capsys.readouterr()
    assert "outside the project" in captured.err
    assert "secret" not in captured.out + captured.err


def test_check_refuses_a_link_out_before_showing_any_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _project(tmp_path / "p", fake_home)
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')

    assert loadout.main(["check", "--root", str(root)]) == 3

    captured = capsys.readouterr()
    assert "outside the project" in captured.err
    assert "secret" not in captured.out + captured.err
