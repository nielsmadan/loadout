"""One profile rule for every run at a root: explicit, else the machine's when the root
is the registered global source, else `default` (review 2.7)."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.git_hook_commands import run_hook
from loadout.machine import MachineConfig, profile_for, resolve_profile

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
    """The machine's profile renders first, so its output is there to lose."""
    source = _registered_source(tmp_path, fake_home)
    assert loadout.main(["sync", "--root", str(source)]) == 0
    assert (fake_home / "away.md").is_file()

    assert loadout.main(["sync", "--root", str(source), "--profile", "default"]) == 0

    assert (fake_home / "here.md").is_file()
    assert not (fake_home / "away.md").exists()


def test_a_root_that_is_not_the_registered_source_uses_default(
    tmp_path: Path, fake_home: Path
) -> None:
    _registered_source(tmp_path, fake_home)

    assert resolve_profile(tmp_path / "elsewhere") == "default"


def test_hook_event_resolves_the_profile_when_it_runs(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 2.1: a hook installed under one machine profile kept rendering it after
    the machine moved to another, and a manual sync then retired the hook's outputs."""
    source = _registered_source(tmp_path, fake_home)

    assert loadout.main(["hook-event", "post-merge", "--root", str(source)]) == 0

    assert (fake_home / "away.md").is_file()


def _break_machine_config(home: Path, source: Path) -> None:
    (home / ".config/loadout/config.toml").write_text(
        f'source = "{source}"\nprofile = "autonomous"\nprofle = "typo"\n'
    )


def test_an_unreadable_machine_config_stops_a_global_source_sync(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 2.2: it rendered `default` and retired the profile's outputs."""
    source = _registered_source(tmp_path, fake_home)
    assert loadout.main(["sync", "--root", str(source)]) == 0
    _break_machine_config(fake_home, source)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(source)]) == 3

    assert "profle" in capsys.readouterr().err
    assert (fake_home / "away.md").is_file()


def test_an_unreadable_machine_config_leaves_a_project_on_default(
    tmp_path: Path, fake_home: Path
) -> None:
    source = _registered_source(tmp_path, fake_home)
    _break_machine_config(fake_home, source)
    project = tmp_path / "project"
    (project / "loadout").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["claude"]\n')

    assert resolve_profile(project) == "default"


def test_a_hook_falls_back_to_default_on_an_unreadable_machine_config(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _registered_source(tmp_path, fake_home)
    _break_machine_config(fake_home, source)

    assert loadout.main(["hook-event", "post-merge", "--root", str(source)]) == 0

    assert (fake_home / "here.md").is_file()


def test_a_loaded_machine_config_names_the_profile_of_its_own_source(tmp_path: Path) -> None:
    """Review 2, 4.6: the global commands pass the config they loaded instead of a reread."""
    source = tmp_path / "source"
    config = MachineConfig(source=source, profile="work")

    assert profile_for(source, None, config) == "work"
    assert profile_for(source, "home", config) == "home"
    assert profile_for(tmp_path / "elsewhere", None, config) == "default"
    assert profile_for(source, None, None) == "default"
