from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from loadout.composition import render
from loadout.errors import LoadoutError
from loadout.manifest import InstructionTarget, Manifest
from loadout.sources import ARTIFACT_TYPES, Source
from loadout.targets import HEADER


def make_source(tmp_path: Path, name: str, fragments: dict[str, str]) -> Source:
    frag_dir = tmp_path / name / "global" / "fragments"
    frag_dir.mkdir(parents=True, exist_ok=True)
    for fragment, body in fragments.items():
        (frag_dir / f"{fragment}.md").write_text(body, encoding="utf-8")
    return Source(name=name, path=tmp_path / name, use=ARTIFACT_TYPES)


@pytest.fixture
def two_sources(tmp_path: Path) -> Manifest:
    company = make_source(tmp_path, "company", {"security": "SECURITY", "shared": "CO"})
    team = make_source(tmp_path, "team", {"review": "REVIEW", "shared": "TEAM"})
    target = InstructionTarget(
        path=PurePosixPath("out.md"),
        fragments=("security", "review"),
        destinations=(),
    )
    return Manifest(sources=(company, team), targets=(target,))


def test_fragments_are_taken_from_both_sources(two_sources: Manifest) -> None:
    rendered = render(two_sources.targets[0], two_sources)
    assert rendered == f"{HEADER}\n\nSECURITY\n\nREVIEW\n"


def test_reversing_the_source_set_changes_nothing(two_sources: Manifest) -> None:
    reversed_manifest = Manifest(
        sources=tuple(reversed(two_sources.sources)), targets=two_sources.targets
    )
    assert render(two_sources.targets[0], two_sources) == render(
        two_sources.targets[0], reversed_manifest
    )


def test_ambiguous_name_names_both_candidates(two_sources: Manifest) -> None:
    target = InstructionTarget(path=PurePosixPath("out.md"), fragments=("shared",), destinations=())
    with pytest.raises(LoadoutError) as excinfo:
        render(target, two_sources)
    message = str(excinfo.value)
    assert "company/shared" in message
    assert "team/shared" in message


def test_qualifying_resolves_the_ambiguity(two_sources: Manifest) -> None:
    for source, body in (("company", "CO"), ("team", "TEAM")):
        target = InstructionTarget(
            path=PurePosixPath("out.md"),
            fragments=(f"{source}/shared",),
            destinations=(),
        )
        assert render(target, two_sources) == f"{HEADER}\n\n{body}\n"
