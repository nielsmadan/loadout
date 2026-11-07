"""Stealth outputs are compared whole; init never adopts a stealth source, so no extractor reads them back."""

from __future__ import annotations

from pathlib import Path

from fixture_root import EXPECTED, STEALTH_NAME, build_stealth, render_stealth_expected
from loadout.stealth_manifest import load_stealth_manifest
from loadout.stealth_render import gather_facts, render_stealth


def test_the_stealth_source_renders_exactly_its_expected_files(tmp_path: Path) -> None:
    expected = {
        str(path.relative_to(EXPECTED)): path.read_text(encoding="utf-8")
        for path in sorted((EXPECTED / "stealth").rglob("*"))
        if path.is_file()
    }
    assert render_stealth_expected(tmp_path) == expected


def test_a_stealth_source_with_agent_sources_writes_no_droid_or_agent_files(tmp_path: Path) -> None:
    folder, target = build_stealth(tmp_path)
    (folder / "agents").mkdir()
    (folder / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews diffs.\n---\nReview the diff.\n",
        encoding="utf-8",
    )
    rendered = render_stealth(
        load_stealth_manifest(folder), STEALTH_NAME, target, gather_facts(target)
    )
    written = {path.relative_to(target).as_posix() for path in rendered.files}
    assert ".factory/AGENTS.md" in written
    assert sorted(p for p in written if "/droids/" in p or "/agents/" in p) == []
