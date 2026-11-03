"""A machine config that does not parse is named as one by every command, init included.

Init global used to treat it as someone else's registration, so `--registration replace`
overwrote a file the user was never told was broken.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

GARBAGE = b"source = = nope\n"


@pytest.fixture
def machine(tmp_path: Path, fake_home: Path, capsys) -> Path:
    source = tmp_path / "agent-config"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (fake_home / ".claude").mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    config = fake_home / ".config/loadout/config.toml"
    config.write_bytes(GARBAGE)
    capsys.readouterr()
    return config


def _refusal(config: Path) -> str:
    return (
        f"loadout: {config}: invalid TOML: Invalid value (at line 1, column 10); "
        "fix it or delete it\n"
    )


@pytest.mark.parametrize("extra", [["--dry-run"], ["--registration", "replace", "--yes"]])
def test_init_global_names_a_machine_config_that_does_not_parse(
    tmp_path: Path, machine: Path, capsys, extra: list[str]
) -> None:
    argv = ["init", "global", str(tmp_path / "agent-config"), "--harness", "claude", *extra]
    assert loadout.main(argv) == 3
    assert capsys.readouterr().err == _refusal(machine)
    assert machine.read_bytes() == GARBAGE


@pytest.mark.parametrize(
    "command", [["sync", "--global"], ["check", "--global"], ["integrate", "skill", "status"]]
)
def test_other_commands_name_it_the_same_way(machine: Path, capsys, command: list[str]) -> None:
    assert loadout.main(command) == 3
    assert capsys.readouterr().err == _refusal(machine)


@pytest.mark.parametrize(
    "extra", [["--dry-run"], ["--yes"], ["--registration", "replace", "--yes"]]
)
def test_init_global_names_a_machine_config_that_is_a_folder(
    tmp_path: Path, machine: Path, capsys, extra: list[str]
) -> None:
    machine.unlink()
    machine.mkdir()
    argv = ["init", "global", str(tmp_path / "agent-config"), "--harness", "claude", *extra]

    assert loadout.main(argv) == 3
    assert capsys.readouterr().err == (
        f"loadout: {machine}: cannot be read: Is a directory; fix it or delete it\n"
    )
    assert machine.is_dir()


def test_a_machine_config_of_the_wrong_shape_ends_the_same_way(machine: Path, capsys) -> None:
    machine.write_text("source = 3\n")

    assert loadout.main(["sync", "--global"]) == 3
    assert capsys.readouterr().err == (
        f"loadout: {machine}: source must be a non-empty string naming a directory; "
        "fix it or delete it\n"
    )


def test_a_machine_config_that_is_not_utf8_ends_the_same_way(machine: Path, capsys) -> None:
    machine.write_bytes(b"\xff\n")

    assert loadout.main(["sync", "--global"]) == 3
    assert capsys.readouterr().err == (
        f"loadout: {machine}: not UTF-8: invalid start byte; fix it or delete it\n"
    )
