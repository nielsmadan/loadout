from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import load_fragment
from loadout.errors import LoadoutError


def test_load_fragment_strips_surrounding_whitespace(tmp_path: Path) -> None:
    (tmp_path / "alpha.md").write_text("\n\n## Alpha\n\nbody\n\n\n")
    assert load_fragment(tmp_path, "alpha") == "## Alpha\n\nbody"


def test_load_fragment_supports_dotted_variant_names(tmp_path: Path) -> None:
    (tmp_path / "git-policy.autonomous.md").write_text("autonomous rules\n")
    assert load_fragment(tmp_path, "git-policy.autonomous") == "autonomous rules"


def test_load_fragment_raises_on_missing_fragment(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as excinfo:
        load_fragment(tmp_path, "nope")
    assert "nope.md" in str(excinfo.value)
