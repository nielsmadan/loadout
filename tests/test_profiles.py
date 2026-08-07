from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global
from loadout.errors import LoadoutError

PROFILED_MANIFEST = """
[[source]]
name = "test"
path = "."

[instructions.plain]
output = "out/plain.md"
order = ["plain"]

[instructions.normal]
output = "out/normal.md"
profile = "normal"
order = ["normal"]

[instructions.auto]
output = "out/auto.md"
profile = "autonomous"
order = ["auto"]
"""

NO_PROFILE_MANIFEST = """
[[source]]
name = "test"
path = "."

[instructions.plain]
output = "out/plain.md"
order = ["plain"]
"""

UNPROFILED_PERMISSION_MANIFEST = """
[[source]]
name = "test"
path = "."

[instructions.auto]
output = "out/auto.md"
profile = "autonomous"
order = ["auto"]

[permissions.codex]
output = "out/perm.rules"
render = "codex"
"""

DESELECTED_PERMISSION_MANIFEST = """
[[source]]
name = "test"
path = "."

[instructions.plain]
output = "out/plain.md"
order = ["plain"]

[permissions.codex]
output = "out/perm.rules"
render = "codex"
profile = "normal"
"""


def build(tmp_path: Path, manifest_body: str) -> Path:
    (tmp_path / "loadout.toml").write_text(manifest_body, encoding="utf-8")
    fragments = tmp_path / "global" / "fragments"
    fragments.mkdir(parents=True, exist_ok=True)
    for name in ("plain", "normal", "auto"):
        (fragments / f"{name}.md").write_text(f"{name} fragment\n", encoding="utf-8")
    return tmp_path


def paths(rendered: dict[Path, str]) -> set[str]:
    return {"/".join(path.parts[-2:]) for path in rendered}


def test_default_profile_renders_only_unprofiled_targets(tmp_path: Path) -> None:
    root = build(tmp_path, PROFILED_MANIFEST)
    assert paths(render_global(root)) == {"out/plain.md"}


def test_manifest_without_any_profiles_renders_under_default(tmp_path: Path) -> None:
    """Guards rule 4's exception: 'default' must not error for lack of a declarer."""
    root = build(tmp_path, NO_PROFILE_MANIFEST)
    assert paths(render_global(root)) == {"out/plain.md"}


def test_unprofiled_target_renders_under_a_non_default_profile(tmp_path: Path) -> None:
    """Guards rule 3: absence of a profile must not be treated as profile == 'default'."""
    root = build(tmp_path, PROFILED_MANIFEST)
    assert "out/plain.md" in paths(render_global(root, profile="autonomous"))
    assert "out/plain.md" in paths(render_global(root, profile="normal"))


def test_unprofiled_permission_target_renders_under_a_non_default_profile(tmp_path: Path) -> None:
    """Guards rule 3 for permission targets specifically, not just instructions."""
    root = build(tmp_path, UNPROFILED_PERMISSION_MANIFEST)
    permissions_dir = root / "permissions"
    permissions_dir.mkdir(parents=True, exist_ok=True)
    (permissions_dir / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    assert paths(render_global(root, profile="autonomous")) == {"out/auto.md", "out/perm.rules"}


def test_profile_selects_its_target(tmp_path: Path) -> None:
    root = build(tmp_path, PROFILED_MANIFEST)
    assert paths(render_global(root, profile="autonomous")) == {"out/plain.md", "out/auto.md"}


def test_unknown_profile_is_an_error(tmp_path: Path) -> None:
    root = build(tmp_path, PROFILED_MANIFEST)
    with pytest.raises(LoadoutError, match="autonmous"):
        render_global(root, profile="autonmous")


def test_error_lists_the_declared_profiles(tmp_path: Path) -> None:
    root = build(tmp_path, PROFILED_MANIFEST)
    with pytest.raises(LoadoutError) as caught:
        render_global(root, profile="nope")
    assert "autonomous" in str(caught.value)
    assert "normal" in str(caught.value)


def test_deselected_permission_target_needs_no_rule_source(tmp_path: Path) -> None:
    root = build(tmp_path, DESELECTED_PERMISSION_MANIFEST)
    assert paths(render_global(root)) == {"out/plain.md"}
