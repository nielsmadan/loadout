"""Overlay outputs are compared whole; init never adopts an overlay, so no extractor reads them back."""

from __future__ import annotations

from pathlib import Path

from fixture_root import EXPECTED, OVERLAY_NAME, build_overlay, render_overlay_expected
from loadout.overlay_manifest import load_overlay_manifest
from loadout.overlay_render import gather_facts, render_overlay


def test_the_overlay_renders_exactly_its_expected_files(tmp_path: Path) -> None:
    expected = {
        str(path.relative_to(EXPECTED)): path.read_text(encoding="utf-8")
        for path in sorted((EXPECTED / "overlay").rglob("*"))
        if path.is_file()
    }
    assert render_overlay_expected(tmp_path) == expected


def test_an_overlay_with_agent_sources_writes_no_droid_or_agent_files(tmp_path: Path) -> None:
    folder, target = build_overlay(tmp_path)
    (folder / "agents").mkdir()
    (folder / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Reviews diffs.\n---\nReview the diff.\n",
        encoding="utf-8",
    )
    rendered = render_overlay(
        load_overlay_manifest(folder), OVERLAY_NAME, target, gather_facts(target)
    )
    written = {path.relative_to(target).as_posix() for path in rendered.files}
    assert ".factory/AGENTS.md" in written
    assert sorted(p for p in written if "/droids/" in p or "/agents/" in p) == []
