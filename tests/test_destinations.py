from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.emit import render_global, write_all
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


CO_OWNED_DESTINATIONS = """
[[source]]
name = "test"
path = "."

[permissions.opencode]
output       = "out/opencode.json"
destinations = ["~/.config/opencode/opencode.json", "~/.opencode-alt.json"]
render       = "opencode"
preserve     = ["mcp"]
"""

DESTINATION_ONLY_CO_OWNED = """
[[source]]
name = "test"
path = "."

[permissions.opencode]
destinations = ["~/.config/opencode/opencode.json"]
render       = "opencode"
preserve     = ["mcp"]
"""


def seed_mcp(path: Path, server: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mcp": {server: {}}}, indent=2) + "\n", encoding="utf-8")


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


def test_each_destination_preserves_its_own_foreign_keys(tmp_path: Path) -> None:
    """Rendering is per output path, so a co-owner writing one destination cannot
    have its key stamped onto the others."""
    root = build(tmp_path, CO_OWNED_DESTINATIONS)
    seed_mcp(root / "out" / "opencode.json", "in_repo")
    seed_mcp(Path.home() / ".config/opencode/opencode.json", "on_machine")
    seed_mcp(Path.home() / ".opencode-alt.json", "elsewhere")

    rendered = render_global(root)
    assert json.loads(rendered[root / "out/opencode.json"])["mcp"] == {"in_repo": {}}
    assert json.loads(rendered[Path.home() / ".config/opencode/opencode.json"])["mcp"] == {
        "on_machine": {}
    }
    assert json.loads(rendered[Path.home() / ".opencode-alt.json"])["mcp"] == {"elsewhere": {}}


def test_a_destination_only_target_preserves_from_the_destination(tmp_path: Path) -> None:
    """With no in-repo output there is no staged copy to read foreign keys back from,
    so the destination itself has to be the source of them."""
    root = build(tmp_path, DESTINATION_ONLY_CO_OWNED)
    destination = Path.home() / ".config/opencode/opencode.json"
    seed_mcp(destination, "on_machine")

    document = json.loads(render_global(root)[destination])
    assert document["mcp"] == {"on_machine": {}}
    assert list(document)[-1] == "mcp"


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


NO_OUTPUT_INSTRUCTION = """
[[source]]
name = "test"
path = "."

[instructions.solo]
destinations = ["~/.claude/CLAUDE.md"]
order = ["plain"]
"""

NO_OUTPUT_PERMISSION = """
[[source]]
name = "test"
path = "."

[permissions.codex]
destinations = ["~/.codex/perm.rules"]
render       = "codex"
"""

NO_OUTPUT_NO_DESTINATIONS = """
[[source]]
name = "test"
path = "."

[instructions.solo]
order = ["plain"]
"""

MIXED_OUTPUT_AND_NO_OUTPUT = """
[[source]]
name = "test"
path = "."

[instructions.solo]
output       = "out/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order = ["plain"]

[instructions.shared]
destinations = ["~/.codex/AGENTS.md"]
order = ["plain"]

[permissions.codex]
output = "out/perm.rules"
render = "codex"

[permissions.pi]
destinations = ["~/.pi/perm.json"]
render       = "pi"
"""

COLLISION_DESTINATION_VS_OTHERS_OUTPUT = """
[[source]]
name = "test"
path = "."

[instructions.owner]
output = "out/owner.md"
order = ["plain"]

[instructions.raider]
destinations = ["{owner_output}"]
order = ["plain"]
"""

COLLIDING_DESTINATIONS_NO_OUTPUT = """
[[source]]
name = "test"
path = "."

[instructions.one]
destinations = ["~/.shared-dest.md"]
order = ["plain"]

[instructions.two]
output       = "out/two.md"
destinations = ["~/.shared-dest.md"]
order = ["plain"]
"""


def test_instruction_target_without_output_renders_only_to_its_destination(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, NO_OUTPUT_INSTRUCTION)
    rendered = render_global(root)
    dest = Path.home() / ".claude/CLAUDE.md"
    assert dest in rendered
    assert rendered[dest] != ""
    # No output path was declared, so nothing lands in the repo root at all.
    assert not any(root in path.parents for path in rendered)


def test_permission_target_without_output_renders_only_to_its_destination(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, NO_OUTPUT_PERMISSION)
    rendered = render_global(root)
    dest = Path.home() / ".codex/perm.rules"
    assert dest in rendered
    assert rendered[dest] != ""
    assert not any(root in path.parents for path in rendered)


def test_instruction_target_without_output_writes_no_file_at_the_repo_root(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, NO_OUTPUT_INSTRUCTION)
    write_all(root)
    assert not (root / "out").exists()
    assert (Path.home() / ".claude/CLAUDE.md").is_file()


def test_target_without_output_or_destinations_is_rejected(tmp_path: Path) -> None:
    root = build(tmp_path, NO_OUTPUT_NO_DESTINATIONS)
    with pytest.raises(LoadoutError, match=r"instructions\.solo"):
        render_global(root)


def test_mixed_manifest_renders_targets_with_and_without_output_together(
    tmp_path: Path,
) -> None:
    root = build(tmp_path, MIXED_OUTPUT_AND_NO_OUTPUT)
    rendered = render_global(root)

    # Only the two targets that declared `output` land under the repo root.
    under_root = {p for p in rendered if root in p.parents}
    assert under_root == {root / "out/CLAUDE.md", root / "out/perm.rules"}

    assert (Path.home() / ".claude/CLAUDE.md") in rendered
    assert (Path.home() / ".codex/AGENTS.md") in rendered
    assert (Path.home() / ".pi/perm.json") in rendered


def test_destination_collides_with_anothers_output_even_without_its_own_output(
    tmp_path: Path,
) -> None:
    owner_output = (tmp_path / "out" / "owner.md").as_posix()
    root = build(tmp_path, COLLISION_DESTINATION_VS_OTHERS_OUTPUT.format(owner_output=owner_output))
    with pytest.raises(LoadoutError, match="destination") as caught:
        render_global(root)
    assert "out/owner.md" in str(caught.value)


def test_two_destinations_collide_when_one_target_has_no_output(tmp_path: Path) -> None:
    root = build(tmp_path, COLLIDING_DESTINATIONS_NO_OUTPUT)
    with pytest.raises(LoadoutError, match="destination") as caught:
        render_global(root)
    assert "out/two.md" in str(caught.value)
