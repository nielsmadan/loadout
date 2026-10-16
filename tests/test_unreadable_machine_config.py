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
