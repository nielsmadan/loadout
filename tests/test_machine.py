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
    assert config.harnesses == ()


def test_profile_is_read(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    path = write(tmp_path, f'source = "{target}"\nprofile = "autonomous"\n')
    config = load_machine_config(path)
    assert config is not None
    assert config.profile == "autonomous"


def test_harness_defaults_are_read(tmp_path: Path) -> None:
    target = tmp_path / "src"
    target.mkdir()
    path = write(tmp_path, f'source = "{target}"\nharnesses = ["claude", "droid"]\n')
    config = load_machine_config(path)
    assert config is not None
    assert config.harnesses == ("claude", "droid")


@pytest.mark.parametrize(
    "value",
    (
        "[]",
        '"claude"',
        '["claude", 1]',
        '["claude", "claude"]',
        '["emacs"]',
    ),
)
def test_invalid_harness_defaults_are_rejected(tmp_path: Path, value: str) -> None:
    target = tmp_path / "src"
    target.mkdir()
    path = write(tmp_path, f'source = "{target}"\nharnesses = {value}\n')
    with pytest.raises(LoadoutError, match="harness"):
        load_machine_config(path)


def test_missing_source_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, 'profile = "autonomous"\n')
    with pytest.raises(LoadoutError, match="source"):
        load_machine_config(path)


def test_source_that_does_not_exist_names_the_config(tmp_path: Path) -> None:
    path = write(tmp_path, 'source = "/nope/missing"\n')
    with pytest.raises(LoadoutError) as caught:
        load_machine_config(path)
    assert str(caught.value).startswith(f"{path}: ")


@pytest.mark.parametrize("source", [".", "ac/loadout", "../elsewhere"])
@pytest.mark.parametrize("require_source", [True, False])
def test_a_relative_source_is_an_error(tmp_path: Path, source: str, require_source: bool) -> None:
    """Review 3, 1.4: `source = "."` registered whatever folder a command ran in."""
    path = write(tmp_path, f'source = "{source}"\n')
    with pytest.raises(LoadoutError, match=rf"^{path}: source {source!r} must be an absolute path"):
        load_machine_config(path, require_source=require_source)


def test_a_home_relative_source_is_absolute_once_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "ac").mkdir()
    config = load_machine_config(write(tmp_path, 'source = "~/ac"\n'))
    assert config is not None
    assert config.source == (tmp_path / "ac").resolve()


def test_harness_defaults_can_be_read_from_a_stale_global_registration(tmp_path: Path) -> None:
    path = write(tmp_path, 'source = "/nope/missing"\nharnesses = ["droid"]\n')
    config = load_machine_config(path, require_source=False)
    assert config is not None
    assert config.harnesses == ("droid",)


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
