"""Overlay outputs are compared whole; init never adopts an overlay, so no extractor reads them back."""

from __future__ import annotations

from pathlib import Path

from fixture_root import EXPECTED, render_overlay_expected


def test_the_overlay_renders_exactly_its_expected_files(tmp_path: Path) -> None:
    expected = {
        str(path.relative_to(EXPECTED)): path.read_text(encoding="utf-8")
        for path in sorted((EXPECTED / "overlay").rglob("*"))
        if path.is_file()
    }
    assert render_overlay_expected(tmp_path) == expected
