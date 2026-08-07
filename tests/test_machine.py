from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.machine import load_machine_config, machine_config_path


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_xdg_config_home_wins() -> None:
    got = machine_config_path({"XDG_CONFIG_HOME": "/x/cfg", "HOME": "/home/u"})
    assert got == Path("/x/cfg/loadout/config.toml")


def test_falls_back_to_home_dot_config() -> None:
    got = machine_config_path({"HOME": "/home/u"})
    assert got == Path("/home/u/.config/loadout/config.toml")


def test_empty_xdg_config_home_is_ignored() -> None:
    got = machine_config_path({"XDG_CONFIG_HOME": "", "HOME": "/home/u"})
    assert got == Path("/home/u/.config/loadout/config.toml")


def test_absent_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_machine_config(tmp_path / "config.toml") is None


def test_source_is_expanded_and_resolved(tmp_path: Path) -> None:
    target = tmp_path / "ac" / "loadout"
    target.mkdir(parents=True)
    path = write(tmp_path, f'source = "{target}"\n')
    config = load_machine_config(path)
    assert config is not None
    assert config.source == target
    assert config.profile is None


def test_profile_is_read(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    path = write(tmp_path, f'source = "{target}"\nprofile = "autonomous"\n')
    config = load_machine_config(path)
    assert config is not None
    assert config.profile == "autonomous"


def test_missing_source_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, 'profile = "autonomous"\n')
    with pytest.raises(LoadoutError, match="source"):
        load_machine_config(path)


def test_source_that_does_not_exist_names_the_config(tmp_path: Path) -> None:
    path = write(tmp_path, 'source = "/nope/missing"\n')
    with pytest.raises(LoadoutError) as caught:
        load_machine_config(path)
    assert str(caught.value).startswith(f"{path}: ")


def test_unknown_key_is_an_error_not_ignored(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    path = write(tmp_path, f'source = "{target}"\nprofil = "typo"\n')
    with pytest.raises(LoadoutError, match="profil"):
        load_machine_config(path)


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = write(tmp_path, "source = [\n")
    with pytest.raises(LoadoutError, match="invalid TOML"):
        load_machine_config(path)
