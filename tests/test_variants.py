from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global
from loadout.errors import LoadoutError

BASE = """
[[source]]
name = "test"
path = "."

[pi]
instructions = ["intro", "policy"]
"""

VARIANT_PROFILE = """
extends  = "default"
variants = ["autonomous"]
"""


def build(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(BASE, encoding="utf-8")
    (tmp_path / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    f = tmp_path / "instructions"
    f.mkdir(exist_ok=True)
    (f / "intro.md").write_text("shared intro\n", encoding="utf-8")
    (f / "policy.md").write_text("interactive policy\n", encoding="utf-8")
    (f / "policy.autonomous.md").write_text("autonomous policy\n", encoding="utf-8")
    return tmp_path


def instructions(root: Path, profile: str = "default") -> str:
    return next(t for p, t in render_global(root, profile=profile).items() if p.suffix == ".md")


def test_a_variant_replaces_only_the_slot_that_has_one(tmp_path: Path) -> None:
    """The collapse §6 exists for: one tag, not a restated order.

    `~/ac`'s autonomous profile listed ten fragments to differ in two.
    """
    root = build(tmp_path)
    (root / "autonomous.toml").write_text(VARIANT_PROFILE, encoding="utf-8")
    text = instructions(root, "autonomous")
    assert "autonomous policy" in text, "the variant must win where it exists"
    assert "interactive policy" not in text
    assert "shared intro" in text, "a slot with no variant falls back to the bare name"


def test_the_default_profile_is_unaffected(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "autonomous.toml").write_text(VARIANT_PROFILE, encoding="utf-8")
    assert "interactive policy" in instructions(root)


def test_an_already_suffixed_name_is_taken_literally(tmp_path: Path) -> None:
    """Otherwise `policy.autonomous` would look for `policy.autonomous.autonomous`."""
    root = build(tmp_path)
    (root / "loadout.toml").write_text(
        BASE.replace('["intro", "policy"]', '["intro", "policy.autonomous"]'), encoding="utf-8"
    )
    (root / "autonomous.toml").write_text(VARIANT_PROFILE, encoding="utf-8")
    assert "autonomous policy" in instructions(root, "autonomous")


def test_variants_are_tried_most_specific_first(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "instructions" / "policy.afk.md").write_text("afk policy\n", encoding="utf-8")
    (root / "afk.toml").write_text(
        'extends = "default"\nvariants = ["afk", "autonomous"]\n', encoding="utf-8"
    )
    assert "afk policy" in instructions(root, "afk")


def test_a_missing_fragment_still_errors_with_the_bare_name(tmp_path: Path) -> None:
    """Variant fallback must not turn a typo into a silent miss."""
    root = build(tmp_path)
    (root / "loadout.toml").write_text(
        BASE.replace('["intro", "policy"]', '["intro", "nosuch"]'), encoding="utf-8"
    )
    (root / "autonomous.toml").write_text(VARIANT_PROFILE, encoding="utf-8")
    with pytest.raises(LoadoutError, match="nosuch"):
        render_global(root, profile="autonomous")
