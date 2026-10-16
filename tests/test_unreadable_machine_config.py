"""A machine config loadout cannot read is named, never a traceback, and the paths
documented to fall back do (review 4, batch 2.5)."""

from __future__ import annotations

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
