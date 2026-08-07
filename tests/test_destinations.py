from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global
from loadout.errors import LoadoutError

MANIFEST_WITH_DESTINATIONS = """
[[source]]
name = "test"
path = "."

[instructions.solo]
output       = "out/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order = ["plain"]

[instructions.shared]
output       = "out/AGENTS.md"
destinations = ["~/.codex/AGENTS.md", "~/.gemini/GEMINI.md", "~/.pi/agent/AGENTS.md"]
order = ["plain"]

[permissions.codex]
output       = "out/perm.rules"
destinations = ["~/.codex/perm.rules"]
render       = "codex"
"""

COLLIDING_DESTINATIONS = """
[[source]]
name = "test"
path = "."

[instructions.one]
output       = "out/one.md"
destinations = ["~/.shared-dest.md"]
order = ["plain"]

[instructions.two]
output       = "out/two.md"
destinations = ["~/.shared-dest.md"]
order = ["plain"]
"""

PROFILED_SHARED_DESTINATION = """
[[source]]
name = "test"
path = "."

[instructions.normal]
output       = "out/normal.md"
destinations = ["~/.shared-dest.md"]
profile      = "normal"
order = ["plain"]

[instructions.auto]
output       = "out/auto.md"
destinations = ["~/.shared-dest.md"]
profile      = "autonomous"
order = ["plain"]
"""


def build(tmp_path: Path, manifest_body: str) -> Path:
    (tmp_path / "loadout.toml").write_text(manifest_body, encoding="utf-8")
    fragments = tmp_path / "instructions"
    fragments.mkdir(parents=True, exist_ok=True)
    (fragments / "plain.md").write_text("plain fragment\n", encoding="utf-8")

    (tmp_path / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    return tmp_path


def test_destination_receives_the_same_bytes_as_the_output(tmp_path: Path) -> None:
    root = build(tmp_path, MANIFEST_WITH_DESTINATIONS)
    rendered = render_global(root)
    assert rendered[root / "out/AGENTS.md"] == rendered[Path.home() / ".codex/AGENTS.md"]


def test_one_output_fans_out_to_every_destination(tmp_path: Path) -> None:
    root = build(tmp_path, MANIFEST_WITH_DESTINATIONS)
    rendered = render_global(root)
    expected = rendered[root / "out/AGENTS.md"]
    for name in (".codex/AGENTS.md", ".gemini/GEMINI.md", ".pi/agent/AGENTS.md"):
        assert rendered[Path.home() / name] == expected


def test_two_targets_sharing_a_destination_collide(tmp_path: Path) -> None:
    root = build(tmp_path, COLLIDING_DESTINATIONS)
    with pytest.raises(LoadoutError, match="destination") as caught:
        render_global(root)
    assert "out/one.md" in str(caught.value)
    assert "out/two.md" in str(caught.value)


def test_profiles_resolve_the_collision(tmp_path: Path) -> None:
    root = build(tmp_path, PROFILED_SHARED_DESTINATION)
    shared = Path.home() / ".shared-dest.md"

    autonomous = render_global(root, profile="autonomous")
    assert autonomous[shared] == autonomous[root / "out/auto.md"]

    normal = render_global(root, profile="normal")
    assert normal[shared] == normal[root / "out/normal.md"]


def test_a_destination_matching_another_targets_output_collides(tmp_path: Path) -> None:
    """The claimed map must track output paths too, not just destinations, so a
    destination that happens to name another target's in-repo output raises
    instead of silently overwriting it."""
    owner_output = (tmp_path / "out" / "owner.md").as_posix()
    manifest_body = f"""
[[source]]
name = "test"
path = "."

[instructions.owner]
output = "out/owner.md"
order = ["plain"]

[instructions.raider]
output       = "out/raider.md"
destinations = ["{owner_output}"]
order = ["plain"]
"""
    root = build(tmp_path, manifest_body)
    with pytest.raises(LoadoutError, match="destination") as caught:
        render_global(root)
    assert "out/owner.md" in str(caught.value)
    assert "out/raider.md" in str(caught.value)


def test_permission_target_destination_receives_the_same_bytes_as_the_output(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, MANIFEST_WITH_DESTINATIONS)
    rendered = render_global(root)
    assert rendered[root / "out/perm.rules"] == rendered[Path.home() / ".codex/perm.rules"]
