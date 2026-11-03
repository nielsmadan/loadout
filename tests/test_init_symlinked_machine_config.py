"""A machine config that is a symlink, as dotfiles keep it, is read through the link by init.

Init treated any link as someone else's registration, so the registered source could not be
initialised again, a broken target read as a conflict, and `--registration replace` refused
with an internal message. Loadout still never writes through the link.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration


@pytest.fixture
def linked(tmp_path: Path, fake_home: Path, capsys) -> tuple[Path, Path, Path]:
    source = tmp_path / "agent-config"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    (fake_home / ".claude").mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    config = fake_home / ".config/loadout/config.toml"
    target = tmp_path / "dotfiles/loadout.toml"
    target.parent.mkdir()
    config.rename(target)
    config.symlink_to(target)
    capsys.readouterr()
    return source, config, target


def test_the_registered_source_initialises_again_through_the_link(
    linked: tuple[Path, Path, Path], capsys
) -> None:
    source, config, target = linked
    registration = target.read_bytes()

    for extra in ("--dry-run", "--yes"):
        argv = ["init", "global", str(source), "--harness", "claude", extra]
        assert loadout.main(argv) == 0, capsys.readouterr().err
    assert (config.readlink(), target.read_bytes()) == (target, registration)


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        ("garbage", "invalid TOML: Invalid value (at line 1, column 10)"),
        ("folder", "cannot be read: Is a directory"),
    ],
)
def test_a_broken_target_is_named(
    linked: tuple[Path, Path, Path], capsys, broken: str, reason: str
) -> None:
    source, config, target = linked
    target.unlink()
    if broken == "folder":
        target.mkdir()
    else:
        target.write_text("source = = nope\n")

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--dry-run"]) == 3
    assert capsys.readouterr().err == f"loadout: {config}: {reason}; fix it or delete it\n"


def test_a_registration_to_write_is_not_written_through_the_link(
    tmp_path: Path, linked: tuple[Path, Path, Path], capsys
) -> None:
    _, config, target = linked
    registration = target.read_bytes()
    other = tmp_path / "other"
    other.mkdir()
    subprocess.run(["git", "init", "-q", str(other)], check=True)

    argv = ["init", "global", str(other), "--harness", "claude", "--registration", "replace"]
    assert loadout.main([*argv, "--yes"]) == 3
    assert capsys.readouterr().err == (
        f"loadout: {config} is a symlink to {target}, and loadout does not write through it. "
        f"Put these lines in {target}, or replace the link with a regular file, then run init "
        f'again:\n\nsource = "{other / "loadout"}"\nharnesses = ["claude"]\n\n'
    )
    assert (config.readlink(), target.read_bytes()) == (target, registration)
