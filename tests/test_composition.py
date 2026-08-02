from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import load_fragment
from loadout.errors import LoadoutError


def test_load_fragment_strips_surrounding_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "alpha.md"
    path.write_text("\n\n## Alpha\n\nbody\n\n\n", encoding="utf-8")
    assert load_fragment(path) == "## Alpha\n\nbody"


def test_load_fragment_raises_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError):
        load_fragment(tmp_path / "nope.md")
