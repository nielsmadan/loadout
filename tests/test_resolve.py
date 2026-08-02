from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.resolve import resolve_fragment
from loadout.sources import ARTIFACT_TYPES, Source


def make_source(
    tmp_path: Path, name: str, fragments: list[str], use: frozenset[str] | None = None
) -> Source:
    root = tmp_path / name
    frag_dir = root / "global" / "fragments"
    frag_dir.mkdir(parents=True, exist_ok=True)
    for fragment in fragments:
        (frag_dir / f"{fragment}.md").write_text(f"body of {name}/{fragment}\n", encoding="utf-8")
    return Source(name=name, path=root, use=use or ARTIFACT_TYPES)


def test_unqualified_name_resolves_when_unique(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "company", ["security"]),)
    item = resolve_fragment(sources, "security")
    assert item.source == "company"
    assert item.name == "security"
    assert item.path.read_text(encoding="utf-8") == "body of company/security\n"


def test_qualified_name_selects_the_named_source(tmp_path: Path) -> None:
    sources = (
        make_source(tmp_path, "company", ["git-policy"]),
        make_source(tmp_path, "team", ["git-policy"]),
    )
    assert resolve_fragment(sources, "team/git-policy").source == "team"
    assert resolve_fragment(sources, "company/git-policy").source == "company"


def test_ambiguous_unqualified_name_is_an_error_naming_both_sources(tmp_path: Path) -> None:
    sources = (
        make_source(tmp_path, "company", ["git-policy"]),
        make_source(tmp_path, "team", ["git-policy"]),
    )
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment(sources, "git-policy")
    message = str(excinfo.value)
    assert "company" in message
    assert "team" in message


def test_missing_name_is_an_error(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "company", ["security"]),)
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment(sources, "nope")
    assert "nope" in str(excinfo.value)


def test_qualified_name_with_unknown_source_is_an_error(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "company", ["security"]),)
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment(sources, "ghost/security")
    assert "ghost" in str(excinfo.value)


def test_qualified_name_with_missing_file_is_an_error(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "company", ["security"]),)
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment(sources, "company/nope")
    assert "company/nope" in str(excinfo.value)


def test_dotted_variant_names_still_work(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "company", ["git-policy.autonomous"]),)
    assert resolve_fragment(sources, "git-policy.autonomous").name == "git-policy.autonomous"


def test_sources_not_using_instructions_are_skipped(tmp_path: Path) -> None:
    sources = (make_source(tmp_path, "perms-only", ["security"], use=frozenset({"permissions"})),)
    with pytest.raises(LoadoutError):
        resolve_fragment(sources, "security")


def test_ambiguity_is_detected_in_either_source_order(tmp_path: Path) -> None:
    a = make_source(tmp_path, "company", ["shared"])
    b = make_source(tmp_path, "team", ["shared"])
    for order in ((a, b), (b, a)):
        with pytest.raises(LoadoutError) as excinfo:
            resolve_fragment(order, "shared")
        message = str(excinfo.value)
        assert "company/shared" in message
        assert "team/shared" in message


def test_fragment_name_cannot_escape_its_source(tmp_path: Path) -> None:
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    sources = (make_source(tmp_path, "company", ["security"]),)
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment(sources, "company/../../outside")
    assert "escapes" in str(excinfo.value)


def test_symlinked_fragment_file_is_rejected_with_a_reason_naming_the_escape(
    tmp_path: Path,
) -> None:
    # A symlinked source or fragments *directory* is fine (the containment
    # check resolves both sides). A symlinked individual fragment *file* is
    # correctly rejected as escaping its source — but the unqualified lookup
    # must say so, not report a bare "not found" that points at the wrong
    # problem.
    (tmp_path / "outside.md").write_text("secret\n", encoding="utf-8")
    company = make_source(tmp_path, "company", ["security"])
    (company.path / "global" / "fragments" / "spliced.md").symlink_to(tmp_path / "outside.md")
    with pytest.raises(LoadoutError) as excinfo:
        resolve_fragment((company,), "spliced")
    message = str(excinfo.value)
    assert "not found in any source" in message
    assert "escaping its source in: company" in message
