from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.resolve import Slice, resolve_item
from loadout.sources import ARTIFACT_TYPES, Source
from loadout.templates import (
    HASH_PREFIX,
    VENDORED,
    resolve_template,
    template_files,
    tree_hash,
    vendored_path,
)

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


def _tree(base: Path, **files: str) -> Path:
    for relative, text in files.items():
        path = base / relative.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return base


def test_the_hash_is_prefixed_with_its_algorithm(tmp_path: Path) -> None:
    digest = tree_hash(_tree(tmp_path / "web", permissions_toml="a\n"))
    assert digest.startswith(HASH_PREFIX)
    assert len(digest) == len(HASH_PREFIX) + 64


def test_the_same_content_hashes_the_same_from_a_different_directory(tmp_path: Path) -> None:
    """No absolute path in the digest — otherwise vendoring would change the hash,
    and one recorded value could not compare a copy against its upstream."""
    first = _tree(tmp_path / "upstream", permissions_toml="a\n", skills__s__SKILL_md="b\n")
    second = _tree(
        tmp_path / "loadout" / "templates" / "web",
        permissions_toml="a\n",
        skills__s__SKILL_md="b\n",
    )
    assert tree_hash(first) == tree_hash(second)


def test_changing_a_byte_changes_the_hash(tmp_path: Path) -> None:
    before = tree_hash(_tree(tmp_path / "web", permissions_toml="a\n"))
    after = tree_hash(_tree(tmp_path / "web", permissions_toml="b\n"))
    assert before != after


def test_moving_content_between_files_changes_the_hash(tmp_path: Path) -> None:
    """The relative path is hashed alongside the bytes, so a rename is a change."""
    before = tree_hash(_tree(tmp_path / "a", one_toml="x\n", two_toml=""))
    after = tree_hash(_tree(tmp_path / "b", one_toml="", two_toml="x\n"))
    assert before != after


def test_a_file_boundary_cannot_be_forged_by_rearranging_bytes(tmp_path: Path) -> None:
    """The byte length is hashed, so two short files cannot collide with one long one."""
    before = tree_hash(_tree(tmp_path / "a", one_toml="xy", two_toml=""))
    after = tree_hash(_tree(tmp_path / "b", one_toml="x", two_toml="y"))
    assert before != after


def test_the_executable_bit_is_part_of_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "web", scripts__run_sh="#!/bin/sh\n")
    before = tree_hash(tree)
    (tree / "scripts" / "run_sh").chmod(0o755)
    assert tree_hash(tree) != before


def test_build_artifacts_are_excluded_from_the_hash(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "web", permissions_toml="a\n")
    clean = tree_hash(tree)
    (tree / "__pycache__").mkdir()
    (tree / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    (tree / ".DS_Store").write_bytes(b"\x00")
    assert tree_hash(tree) == clean


def test_template_files_are_relative_and_sorted(tmp_path: Path) -> None:
    tree = _tree(tmp_path / "web", b_toml="", a__c_toml="", a__b_toml="")
    assert template_files(tree) == (Path("a/b_toml"), Path("a/c_toml"), Path("b_toml"))


def test_an_empty_tree_hashes_rather_than_failing(tmp_path: Path) -> None:
    """`railway` in the live source carries one slice; an empty one must not crash."""
    empty = tmp_path / "railway"
    empty.mkdir()
    assert tree_hash(empty).startswith(HASH_PREFIX)


def _global_source(home: Path, monkeypatch: pytest.MonkeyPatch, *templates: str) -> Path:
    """A machine config pointing at a global source that offers templates.

    Built out in full rather than shortcut, because the chain a declared template
    depends on — machine config, global manifest, its `[[source]]` list — is
    exactly what these tests are for.
    """
    source = home / "ac"
    (source / "loadout").mkdir(parents=True, exist_ok=True)
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
        encoding="utf-8",
    )
    for template in templates:
        (source / "loadout" / "templates" / template).mkdir(parents=True, exist_ok=True)
    xdg = home / ".config"
    (xdg / "loadout").mkdir(parents=True, exist_ok=True)
    (xdg / "loadout" / "config.toml").write_text(f'source = "{source}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return source


def test_a_declared_template_resolves_from_the_global_source(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _global_source(fake_home, monkeypatch, "web")
    found = resolve_template("web", tmp_path)
    assert found.path == source / "loadout" / "templates" / "web"
    assert found.source == "ac"


def test_a_vendored_copy_wins_and_stops_resolution(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _global_source(fake_home, monkeypatch, "web")
    vendored = vendored_path(tmp_path, "web")
    vendored.mkdir(parents=True)
    found = resolve_template("web", tmp_path)
    assert found.path == vendored
    assert found.source == VENDORED


def test_a_vendored_copy_resolves_with_no_machine_config_at_all(tmp_path: Path) -> None:
    """The property that lets a clone build without the template repo."""
    vendored = vendored_path(tmp_path, "web")
    vendored.mkdir(parents=True)
    assert resolve_template("web", tmp_path).path == vendored


def test_an_unresolvable_template_names_every_place_searched(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _global_source(fake_home, monkeypatch, "flutter")
    with pytest.raises(LoadoutError) as error:
        resolve_template("web", tmp_path)
    message = str(error.value)
    assert str(vendored_path(tmp_path, "web")) in message
    assert str(source / "loadout" / "templates" / "web") in message


def test_no_machine_config_says_so_rather_than_reporting_no_sources(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="no machine config"):
        resolve_template("web", tmp_path)


def test_a_source_offering_no_templates_is_left_out_of_the_search(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _global_source(fake_home, monkeypatch, "web")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\nuse = ["skills"]\n\n'
        "[claude]\ninstructions = []\n",
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match="no source offers templates"):
        resolve_template("web", tmp_path)
