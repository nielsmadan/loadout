"""The round trip against the bytes the project actually ships.

tests/fixtures/expected/ is the reviewed output of every renderer over the
fixture source. Extracting from those files and rendering again is the spec's
acceptance criterion — `render(extract(x)) == x` — run over real artifacts
rather than over rules this test made up: a 208-line Claude settings base, a
foreign key preserved from a co-owned file, a server name with a dot, and a
command deliberately listed in two categories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.extract import extract
from loadout.permissions.renderers import RENDERERS, JsonSpec, TextSpec
from loadout.project import PROJECT_PRESET
from test_extract_roundtrip import NOT_INVERTED

EXPECTED = Path(__file__).parent / "fixtures" / "expected"

# The global-scope manifest is tests/fixtures/loadout.toml; each `[permissions.*]`
# block names an output and a renderer, and both profiles emit the same set.
GLOBAL_RENDERERS = {
    "claude.json": "claude",
    "claude-empty.json": "claude",
    "claude-mcp.json": "claude-mcp-permissions",
    "codex.rules": "codex",
    "codex-mcp.toml": "codex-mcp-permissions",
    "opencode.json": "opencode",
    "pi.json": "pi",
}


def _artifacts() -> list[tuple[Path, str]]:
    found = [
        (path, GLOBAL_RENDERERS[path.name])
        for profile in ("default", "variant")
        for path in sorted((EXPECTED / profile / "perm").glob("*"))
        if path.name in GLOBAL_RENDERERS
    ]
    found += [
        (EXPECTED / "project" / spec.output, spec.renderer)
        for slices in PROJECT_PRESET.values()
        for spec in slices.values()
        if spec.output is not None
        and spec.renderer is not None
        # Deferred the same way test_extract_roundtrip.py defers them: no
        # inverse exists yet, so there is nothing this round trip could extract.
        and spec.renderer not in NOT_INVERTED
        and (EXPECTED / "project" / spec.output).is_file()
    ]
    return found


ARTIFACTS = _artifacts()

IDS = [str(path.relative_to(EXPECTED)) for path, _ in ARTIFACTS]


def _load(path: Path, name: str) -> object:
    if isinstance(RENDERERS[name], TextSpec):
        return path.read_text()
    return json.loads(path.read_text())


def _serialize(name: str, document: object) -> str:
    spec = RENDERERS[name]
    if isinstance(spec, TextSpec):
        assert isinstance(document, str)
        return document
    assert isinstance(spec, JsonSpec)
    return json.dumps(document, indent=2, ensure_ascii=spec.ensure_ascii) + "\n"


# The only shipped artifacts extraction cannot reproduce. Listed rather than
# skipped by a truthiness check, so the set cannot quietly grow.
LOSSY = {"default/perm/codex.rules", "variant/perm/codex.rules"}


def _rerender(path: Path, name: str) -> str:
    extraction = extract(name, _load(path, name))
    spec = RENDERERS[name]
    again = (
        spec.fn(extraction.rules)
        if isinstance(spec, TextSpec)
        else spec.fn(extraction.rules, extraction.base)
    )
    return _serialize(name, again)


@pytest.mark.parametrize(
    ("path", "name"),
    [pair for pair, artifact_id in zip(ARTIFACTS, IDS, strict=True) if artifact_id not in LOSSY],
    ids=[artifact_id for artifact_id in IDS if artifact_id not in LOSSY],
)
def test_a_shipped_artifact_extracts_and_renders_back_to_itself(path: Path, name: str) -> None:
    assert _rerender(path, name) == path.read_text()


def test_only_the_listed_artifacts_report_a_loss() -> None:
    reported = {
        artifact_id
        for (path, name), artifact_id in zip(ARTIFACTS, IDS, strict=True)
        if extract(name, _load(path, name)).notes
    }
    assert reported == LOSSY


@pytest.mark.parametrize("profile", ["default", "variant"])
def test_the_shipped_codex_rules_reports_the_globs_it_cannot_categorise(profile: str) -> None:
    """render_codex lists globs in a comment block with no decision attached.

    Nothing in the file says whether `gamma-*` was allowed, asked or denied, so
    it is reported rather than guessed at — and everything Codex *can* state
    still renders back exactly.
    """
    path = EXPECTED / profile / "perm" / "codex.rules"
    extraction = extract("codex", path.read_text())
    assert [note.detail for note in extraction.notes] == [
        "codex: skipped glob 'gamma-*' has no category",
        "codex: skipped glob 'delta run --tag=*' has no category",
    ]
    assert _rerender(path, "codex") == path.read_text().split("\n# Skipped")[0].rstrip("\n") + "\n"


def test_every_shipped_permission_artifact_is_covered() -> None:
    """A new renderer must not slip past this file by landing an artifact nobody reads."""
    on_disk = {
        path.name
        for profile in ("default", "variant")
        for path in (EXPECTED / profile / "perm").glob("*")
    }
    assert on_disk == set(GLOBAL_RENDERERS)
