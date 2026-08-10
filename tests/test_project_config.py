from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.project import KNOWN_HARNESSES, PRESET, ProjectConfig, load_project_config


def write(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "loadout"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "config.toml"
    p.write_text(body, encoding="utf-8")
    return p


def test_parses_the_harness_list(tmp_path: Path) -> None:
    path = write(tmp_path, 'harnesses = ["claude", "codex"]\n')
    assert load_project_config(path) == ProjectConfig(harnesses=("claude", "codex"))


def test_known_harnesses_are_the_five_supported_names() -> None:
    assert frozenset({"claude", "codex", "opencode", "pi"}) == KNOWN_HARNESSES


def test_preset_has_an_entry_for_every_known_harness() -> None:
    """A harness with no PRESET entry would make project_targets raise a bare
    KeyError (exit 4) instead of a LoadoutError (exit 3) — milestone 4 adds no
    new exit codes."""
    assert set(PRESET) == KNOWN_HARNESSES


def test_unknown_harness_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, 'harnesses = ["claude", "emacs"]\n')
    with pytest.raises(LoadoutError, match="emacs"):
        load_project_config(path)


def test_empty_harness_list_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "harnesses = []\n")
    with pytest.raises(LoadoutError, match="at least one harness"):
        load_project_config(path)


def test_missing_harnesses_key_is_an_error(tmp_path: Path) -> None:
    path = write(tmp_path, "\n")
    with pytest.raises(LoadoutError, match="harnesses"):
        load_project_config(path)


def test_unrecognised_key_is_an_error_not_ignored(tmp_path: Path) -> None:
    path = write(tmp_path, 'harnesses = ["claude"]\noutputs = "custom"\n')
    with pytest.raises(LoadoutError, match="outputs"):
        load_project_config(path)


def test_duplicate_harness_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, 'harnesses = ["claude", "claude"]\n')
    with pytest.raises(LoadoutError, match="duplicate"):
        load_project_config(path)


def test_validation_error_names_the_config_file(tmp_path: Path) -> None:
    path = write(tmp_path, 'harnesses = ["claude", "claude"]\n')
    with pytest.raises(LoadoutError) as caught:
        load_project_config(path)
    assert str(caught.value).startswith(f"{path}: ")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="not found"):
        load_project_config(tmp_path / "loadout" / "config.toml")


def test_invalid_toml_raises(tmp_path: Path) -> None:
    path = write(tmp_path, "harnesses = [\n")
    with pytest.raises(LoadoutError, match="invalid TOML"):
        load_project_config(path)
