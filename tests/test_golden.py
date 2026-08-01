from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import render
from loadout.targets import TARGETS, Target

GOLDEN = Path(__file__).parent / "golden"
FRAGMENTS = GOLDEN / "fragments"
EXPECTED = GOLDEN / "expected"


@pytest.mark.parametrize("target", TARGETS, ids=lambda t: str(t.path))
def test_render_matches_frozen_golden(target: Target) -> None:
    expected = (EXPECTED / str(target.path)).read_text()
    assert render(target, FRAGMENTS) == expected


def test_every_target_has_a_frozen_golden() -> None:
    for target in TARGETS:
        assert (EXPECTED / str(target.path)).is_file(), f"no golden for {target.path}"
