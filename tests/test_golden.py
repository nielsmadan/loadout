from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import render
from loadout.errors import LoadoutError
from loadout.manifest import InstructionTarget, Manifest, load_manifest

GOLDEN = Path(__file__).parent / "golden"
EXPECTED = GOLDEN / "expected"


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return load_manifest(GOLDEN / "manifest.toml")


def test_manifest_declares_three_targets(manifest: Manifest) -> None:
    assert len(manifest.targets) == 3


def test_render_matches_frozen_golden(manifest: Manifest) -> None:
    for target in manifest.targets:
        expected = (EXPECTED / str(target.path)).read_text(encoding="utf-8")
        assert render(target, manifest) == expected, f"{target.path} differs from its golden"


def test_source_order_does_not_change_output(manifest: Manifest) -> None:
    reversed_sources = Manifest(sources=tuple(reversed(manifest.sources)), targets=manifest.targets)
    for target in manifest.targets:
        assert render(target, manifest) == render(target, reversed_sources)


def test_every_target_has_a_frozen_golden(manifest: Manifest) -> None:
    for target in manifest.targets:
        assert (EXPECTED / str(target.path)).is_file(), f"no golden for {target.path}"


def test_unknown_fragment_name_raises(manifest: Manifest) -> None:
    broken = InstructionTarget(
        path=manifest.targets[0].path,
        fragments=("no-such-fragment",),
        destinations=(),
    )
    with pytest.raises(LoadoutError):
        render(broken, manifest)
