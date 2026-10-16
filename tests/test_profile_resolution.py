"""One profile rule for every run at a root: explicit, else the machine's when the root
is the registered global source, else `default` (review 2.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.git_hook_commands import run_hook
from loadout.machine import resolve_profile

pytestmark = pytest.mark.integration


def _registered_source(tmp_path: Path, home: Path) -> Path:
    source = tmp_path / "source"
    (source / "instructions").mkdir(parents=True)
    (source / "instructions" / "x.md").write_text("Rules.\n")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.away]\ndestinations = ["~/away.md"]\nprofile = "autonomous"\norder = ["x"]\n\n'
        '[instructions.here]\ndestinations = ["~/here.md"]\nprofile = "default"\norder = ["x"]\n'
    )
    machine = home / ".config" / "loadout"
    machine.mkdir(parents=True)
    (machine / "config.toml").write_text(f'source = "{source}"\nprofile = "autonomous"\n')
    return source


def test_a_root_sync_and_a_hook_sync_render_the_same_profile(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _registered_source(tmp_path, fake_home)
    assert run_hook("post-merge", source, resolve_profile(source), []) == 0
    assert (fake_home / "away.md").is_file()

    assert loadout.main(["sync", "--root", str(source)]) == 0

    assert (fake_home / "away.md").is_file()
    assert not (fake_home / "here.md").exists()


def test_an_explicit_profile_still_wins(tmp_path: Path, fake_home: Path, capsys) -> None:
    source = _registered_source(tmp_path, fake_home)

    assert loadout.main(["sync", "--root", str(source), "--profile", "default"]) == 0

    assert (fake_home / "here.md").is_file()


def test_a_root_that_is_not_the_registered_source_uses_default(
    tmp_path: Path, fake_home: Path
) -> None:
    _registered_source(tmp_path, fake_home)

    assert resolve_profile(tmp_path / "elsewhere") == "default"
