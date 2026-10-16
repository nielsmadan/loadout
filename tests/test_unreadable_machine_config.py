"""A machine config loadout cannot read is named, never a traceback, and the paths
documented to fall back do (review 4, batch 2.5)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration


@pytest.fixture
def unreadable(tmp_path: Path, fake_home: Path) -> Iterator[Path]:
    (tmp_path / "global").mkdir()
    config = register(tmp_path / "global")
    machine = fake_home / ".config/loadout/config.toml"
    machine.chmod(0o000)
    yield config
    machine.chmod(0o600)


def _project(root: Path) -> Path:
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (root / "loadout/permissions.toml").write_text("")
    (root / "loadout/instructions/main.md").write_text("Project rules.\n")
    return root


@pytest.mark.parametrize(
    "command",
    [["sync"], ["check"], ["check", "--json"], ["hook-event", "post-checkout"]],
)
def test_a_project_falls_back_past_an_unreadable_machine_config(
    tmp_path: Path, unreadable: Path, capsys, command: list[str]
) -> None:
    project = _project(tmp_path / "project")
    assert loadout.main(["sync", "--root", str(project)]) == 0
    capsys.readouterr()

    code = loadout.main(
        [
            *command,
            "--root",
            str(project),
            *(["--", "a", "b", "1"] if command[0] == "hook-event" else []),
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert "Project rules." in (project / "AGENTS.md").read_text()


def test_a_global_source_names_the_unreadable_machine_config(
    tmp_path: Path, fake_home: Path, unreadable: Path, capsys
) -> None:
    (unreadable / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(unreadable)]) == 3

    err = capsys.readouterr().err
    assert err.startswith(f"loadout: {fake_home / '.config/loadout/config.toml'}: cannot be read")
    assert "Traceback" not in err


@pytest.fixture
def unreadable_folder(tmp_path: Path, fake_home: Path) -> Iterator[Path]:
    """Review 5, batch 2.2: the existence check sat outside the read, so a folder that
    cannot be listed crashed every command with a traceback."""
    (tmp_path / "global").mkdir()
    register(tmp_path / "global")
    folder = fake_home / ".config/loadout"
    folder.chmod(0o000)
    yield folder
    folder.chmod(0o700)


@pytest.mark.parametrize("command", [["sync"], ["check"]])
def test_a_project_falls_back_past_an_unreadable_config_folder(
    tmp_path: Path, unreadable_folder: Path, capsys, command: list[str]
) -> None:
    project = _project(tmp_path / "project")
    assert loadout.main(["sync", "--root", str(project)]) == 0, capsys.readouterr().err

    assert loadout.main([*command, "--root", str(project)]) == 0, capsys.readouterr().err


def test_a_machine_config_that_is_not_utf_8_is_named(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    (tmp_path / "global").mkdir()
    root = register(tmp_path / "global")
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    machine = fake_home / ".config/loadout/config.toml"
    machine.write_bytes(b'source = "/x\xc3"\n')
    project = _project(tmp_path / "project")
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(project)]) == 0
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert capsys.readouterr().err.startswith(f"loadout: {machine}: not UTF-8")


def test_a_hook_at_the_global_source_refuses_and_says_the_git_operation_completed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 5, batch 4.3: the README said a hook falls back. At the global source it
    refuses as sync does, after git has finished, and says what to run once it is fixed."""
    (tmp_path / "global").mkdir()
    root = register(tmp_path / "global")
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    machine = fake_home / ".config/loadout/config.toml"
    machine.write_text("not [toml\n")
    capsys.readouterr()

    code = loadout.main(["hook-event", "post-merge", "--root", str(root), "--", "0"])

    err = capsys.readouterr().err
    assert code == 3
    assert err.startswith(f"loadout: {machine}: invalid TOML:")
    assert "Git merge already completed; loadout regeneration failed." in err


@pytest.mark.parametrize("shape", ["a folder", "under a file"])
def test_a_machine_config_that_is_no_file_is_named(
    tmp_path: Path, fake_home: Path, capsys, shape: str
) -> None:
    """Review 6, batch 2.2: both read as no machine config at all, so the advice was to
    register one, which init then refused."""
    root = tmp_path / "global"
    root.mkdir()
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    folder = fake_home / ".config/loadout"
    if shape == "a folder":
        (folder / "config.toml").mkdir(parents=True)
    else:
        folder.parent.mkdir(parents=True, exist_ok=True)
        folder.write_text("")
    reason = "Is a directory" if shape == "a folder" else "Not a directory"

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {folder / 'config.toml'}: cannot be read: {reason}\n"
    )


@pytest.mark.parametrize("profile", ["default", "work"])
def test_a_profile_that_is_not_utf_8_is_named(
    tmp_path: Path, fake_home: Path, capsys, profile: str
) -> None:
    """Review 6, batch 3 (gt-declared-badtoml): reading it raised `UnicodeDecodeError`,
    and exit 4."""
    (tmp_path / "global").mkdir()
    root = register(tmp_path / "global")
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    named = root / ("loadout.toml" if profile == "default" else f"{profile}.toml")
    named.write_bytes(b'[[source]]\nname = "s"\npath = "."\n# \xff\n')
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--profile", profile]) == 3

    assert capsys.readouterr().err == f"loadout: {named}: not UTF-8: invalid start byte\n"


def test_a_project_config_that_is_not_utf_8_is_named(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path / "project")
    config = project / "loadout/config.toml"
    config.write_bytes(b'harnesses = ["codex"]\n# \xff\n')

    assert loadout.main(["check", "--root", str(project)]) == 3

    assert capsys.readouterr().err == f"loadout: {config}: not UTF-8: invalid start byte\n"


def test_an_unreadable_machine_config_wins_over_a_staged_manifest(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 6, batch 3 (wa-staged-err): with the config unread, nothing says the folder
    is not the registered source, so unstaging is not the remedy."""
    (tmp_path / "global").mkdir()
    register(tmp_path / "global")
    machine = fake_home / ".config/loadout/config.toml"
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    subprocess.run(["git", "-C", str(clone), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(clone), "add", "-A"], check=True)
    machine.chmod(0o000)
    try:
        # An explicit profile skips the profile lookup, so the staged gate reads the config.
        assert loadout.main(["check", "--staged", "--root", str(clone), "--profile", "p"]) == 3
    finally:
        machine.chmod(0o600)

    assert capsys.readouterr().err == f"loadout: {machine}: cannot be read: Permission denied\n"
