from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.sources import ARTIFACT_TYPES, parse_sources


def test_relative_path_resolves_against_base(tmp_path: Path) -> None:
    (tmp_path / "company").mkdir()
    sources = parse_sources([{"name": "company", "path": "company"}], tmp_path)
    assert sources[0].path == (tmp_path / "company").resolve()


def test_absolute_path_is_kept(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere"
    other.mkdir()
    sources = parse_sources([{"name": "x", "path": str(other)}], tmp_path)
    assert sources[0].path == other.resolve()


def test_omitting_use_takes_every_artifact_type(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    sources = parse_sources([{"name": "a", "path": "a"}], tmp_path)
    assert sources[0].use == ARTIFACT_TYPES


def test_use_narrows(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    sources = parse_sources([{"name": "a", "path": "a", "use": ["instructions"]}], tmp_path)
    assert sources[0].use == frozenset({"instructions"})


def test_unknown_use_value_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(LoadoutError) as excinfo:
        parse_sources([{"name": "a", "path": "a", "use": ["instrucshuns"]}], tmp_path)
    assert "instrucshuns" in str(excinfo.value)


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as excinfo:
        parse_sources([{"name": "a", "path": "nope"}], tmp_path)
    assert "nope" in str(excinfo.value)


def test_duplicate_source_names_are_an_error(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    with pytest.raises(LoadoutError) as excinfo:
        parse_sources([{"name": "dup", "path": "a"}, {"name": "dup", "path": "b"}], tmp_path)
    assert "dup" in str(excinfo.value)


def test_name_is_required(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(LoadoutError):
        parse_sources([{"path": "a"}], tmp_path)


def test_equal_sources_collapse_in_a_set(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    first = parse_sources([{"name": "a", "path": "a"}], tmp_path)[0]
    second = parse_sources([{"name": "a", "path": "a"}], tmp_path)[0]
    assert first is not second
    assert len({first, second}) == 1


def test_non_table_source_entry_is_a_loadout_error_not_a_crash(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as excinfo:
        parse_sources(["a", "b"], tmp_path)  # type: ignore[list-item]
    assert "table" in str(excinfo.value)


def test_empty_use_list_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    with pytest.raises(LoadoutError) as excinfo:
        parse_sources([{"name": "a", "path": "a", "use": []}], tmp_path)
    assert "use" in str(excinfo.value)


def test_sources_differing_by_name_do_not_collapse(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    first = parse_sources([{"name": "a", "path": "a"}], tmp_path)[0]
    second = parse_sources([{"name": "b", "path": "a"}], tmp_path)[0]
    assert len({first, second}) == 2
