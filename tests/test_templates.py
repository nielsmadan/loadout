from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.resolve import Slice, resolve_item
from loadout.sources import ARTIFACT_TYPES, Source

TREE = Slice(use="templates", subdir="templates", suffix="", directory=True)


def _source(base: Path, name: str, *templates: str) -> Source:
    path = base / name
    path.mkdir(parents=True, exist_ok=True)
    for template in templates:
        (path / "templates" / template).mkdir(parents=True)
    return Source(name=name, path=path, use=ARTIFACT_TYPES)


def test_templates_is_an_artifact_type_a_source_may_offer() -> None:
    assert "templates" in ARTIFACT_TYPES


def test_a_directory_slice_resolves_a_tree_rather_than_a_document(tmp_path: Path) -> None:
    source = _source(tmp_path, "company", "web")
    found = resolve_item((source,), "web", TREE)
    assert found.path == source.path / "templates" / "web"
    assert found.source == "company"


def test_a_file_is_not_a_match_for_a_directory_slice(tmp_path: Path) -> None:
    source = _source(tmp_path, "company")
    (source.path / "templates").mkdir(parents=True, exist_ok=True)
    (source.path / "templates" / "web").write_text("not a tree\n", encoding="utf-8")
    with pytest.raises(LoadoutError, match="not found in any source"):
        resolve_item((source,), "web", TREE)


def test_two_sources_offering_one_template_name_is_refused(tmp_path: Path) -> None:
    first = _source(tmp_path, "company", "web")
    second = _source(tmp_path, "personal", "web")
    with pytest.raises(LoadoutError, match="ambiguous across sources"):
        resolve_item((first, second), "web", TREE)


def test_a_qualified_name_disambiguates_a_collision(tmp_path: Path) -> None:
    first = _source(tmp_path, "company", "web")
    _source(tmp_path, "personal", "web")
    found = resolve_item((first, _source(tmp_path, "personal")), "company/web", TREE)
    assert found.path == first.path / "templates" / "web"


def test_a_source_that_does_not_offer_templates_is_not_searched(tmp_path: Path) -> None:
    offered = _source(tmp_path, "company", "web")
    restricted = Source(name="skills-only", path=offered.path, use=frozenset({"skills"}))
    with pytest.raises(LoadoutError, match="not found in any source"):
        resolve_item((restricted,), "web", TREE)


def test_a_template_name_may_not_escape_its_source(tmp_path: Path) -> None:
    """`..` resolves to a real directory, so a directory slice needs the escape
    guard that a document slice got for free from `is_file()`."""
    source = _source(tmp_path, "company", "web")
    with pytest.raises(LoadoutError, match="rejected as escaping its source"):
        resolve_item((source,), "..", TREE)


def test_a_slashed_escape_is_read_as_a_source_qualifier_and_refused(tmp_path: Path) -> None:
    source = _source(tmp_path, "company", "web")
    with pytest.raises(LoadoutError, match="unknown source"):
        resolve_item((source,), "../../etc", TREE)
