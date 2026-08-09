from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.emit import render_global, write_all
from loadout.errors import LoadoutError
from loadout.manifest import load_manifest

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


ENV_DESTINATION = """
[[source]]
name = "test"
path = "."

[instructions.solo]
destinations = ["{template}"]
order = ["plain"]
"""

ENV_PERMISSION_DESTINATION = """
[[source]]
name = "test"
path = "."

[permissions.codex]
destinations = ["{template}"]
render       = "codex"
"""


def env_root(tmp_path: Path, template: str, body: str = ENV_DESTINATION) -> Path:
    return build(tmp_path, body.format(template=template))


def test_a_set_variable_relocates_the_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: a harness that moved its config directory gets written to
    where it actually reads, not to the hardcoded default."""
    relocated = tmp_path / "elsewhere" / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(relocated))
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md")

    write_all(root)
    assert (relocated / "CLAUDE.md").is_file()


def test_an_unset_variable_falls_back_to_the_default_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md")

    write_all(root)
    assert (fake_home / ".claude" / "CLAUDE.md").is_file()


def test_an_empty_variable_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path
) -> None:
    """Matches machine_config_path's reading of XDG_CONFIG_HOME — an exported but
    empty variable takes the fallback rather than writing to `/CLAUDE.md`."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "")
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md")

    write_all(root)
    assert (fake_home / ".claude" / "CLAUDE.md").is_file()


def test_a_variable_with_no_fallback_is_an_error_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    root = env_root(tmp_path, "${CODEX_HOME}/AGENTS.md")

    with pytest.raises(LoadoutError, match="CODEX_HOME") as caught:
        render_global(root)
    assert "instructions.solo" in str(caught.value)


def test_an_empty_fallback_is_an_error_rather_than_expanding_to_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`${VAR:-}` would otherwise resolve to `/CLAUDE.md` — the write the no-fallback
    error exists to prevent, spelled differently."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR:-}/CLAUDE.md")

    with pytest.raises(LoadoutError, match="CLAUDE_CONFIG_DIR"):
        render_global(root)


@pytest.mark.parametrize(
    "template",
    [
        "${CLAUDE_CONFIG_DIR-~/.claude}/CLAUDE.md",  # POSIX's no-colon form
        "${CLAUDE_CONFIG_DIR:?unset}/CLAUDE.md",
        "${CLAUDE_CONFIG_DIR:-${HOME}/.claude}/CLAUDE.md",  # fallback cannot nest
        "${CLAUDE_CONFIG_DIR/CLAUDE.md",  # unclosed
        "${}/CLAUDE.md",
    ],
)
def test_a_reference_the_grammar_does_not_cover_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, template: str
) -> None:
    """Left literal these become path text, so `sync` would create a directory named
    after the template and `check` would then compare against it and report clean."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/opt/claude")
    root = env_root(tmp_path, template)

    with pytest.raises(LoadoutError, match="does not understand"):
        render_global(root)


def test_a_destination_that_resolves_relative_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative destination is written under whatever directory loadout happened to
    be run from, so it must not resolve at all."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative-dir")
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR}/CLAUDE.md")

    with pytest.raises(LoadoutError, match="absolute"):
        render_global(root)


PROFILED_ENV_DESTINATION = """
[[source]]
name = "test"
path = "."

[instructions.here]
destinations = ["HERE_DESTINATION"]
profile      = "here"
order = ["plain"]

[instructions.elsewhere]
destinations = ["${ELSEWHERE_CONFIG_DIR}/CLAUDE.md"]
profile      = "elsewhere"
order = ["plain"]
"""


def test_an_unselected_profiles_variable_does_not_have_to_be_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolution is per selected target. A machine that never runs the `elsewhere`
    profile has no reason to set its variable, and must not be blocked by it."""
    monkeypatch.delenv("ELSEWHERE_CONFIG_DIR", raising=False)
    here = tmp_path / "here" / "CLAUDE.md"
    root = build(tmp_path, PROFILED_ENV_DESTINATION.replace("HERE_DESTINATION", str(here)))

    assert here in render_global(root, profile="here")
    with pytest.raises(LoadoutError, match="ELSEWHERE_CONFIG_DIR"):
        render_global(root, profile="elsewhere")


def test_a_permission_destination_expands_the_same_way(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    relocated = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(relocated))
    root = env_root(tmp_path, "${CODEX_HOME}/rules/perm.rules", ENV_PERMISSION_DESTINATION)

    write_all(root)
    assert (relocated / "rules" / "perm.rules").is_file()


def test_a_variable_substitutes_mid_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Expansion is textual, so a reference away from the start of the entry is
    substituted like any other — the prefix around it survives verbatim."""
    monkeypatch.setenv("HARNESS_DIR", "harness-x")
    root = env_root(tmp_path, str(tmp_path) + "/base/${HARNESS_DIR}/opencode.json")

    write_all(root)
    assert (tmp_path / "base" / "harness-x" / "opencode.json").is_file()


def test_a_destination_with_no_reference_is_left_alone(tmp_path: Path, fake_home: Path) -> None:
    """A literal `$` is not a reference — only the `${...}` form substitutes."""
    root = env_root(tmp_path, "~/.weird$dir/CLAUDE.md")

    write_all(root)
    assert (fake_home / ".weird$dir" / "CLAUDE.md").is_file()


def test_an_expansion_containing_dot_dot_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validation runs on the resolved path — after both `${...}` and `~` — so a
    variable cannot smuggle in the `..` a literal destination is refused for."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.config/../../escape")
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR}/CLAUDE.md")

    with pytest.raises(LoadoutError, match=r"\.\.") as caught:
        render_global(root)
    assert "resolves to" in str(caught.value)


COLLIDING_AFTER_EXPANSION = """
[[source]]
name = "test"
path = "."

[instructions.one]
destinations = ["${CLAUDE_CONFIG_DIR}/CLAUDE.md"]
order = ["plain"]

[instructions.two]
destinations = ["${OTHER_DIR}/CLAUDE.md"]
order = ["plain"]
"""


def test_two_templates_that_expand_to_one_path_collide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Collision detection has to see through the templates: two destinations that
    look different in the manifest but name the same file must still raise.

    The assertions name the resolved path and both owners, so an expansion *failure*
    — which also mentions "destination" and "instructions" — cannot satisfy them."""
    shared = str(tmp_path / "shared")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", shared)
    monkeypatch.setenv("OTHER_DIR", shared)
    root = build(tmp_path, COLLIDING_AFTER_EXPANSION)

    with pytest.raises(LoadoutError, match="claimed by both") as caught:
        render_global(root)
    message = str(caught.value)
    assert f"{shared}/CLAUDE.md" in message
    assert "instructions.one" in message
    assert "instructions.two" in message


def test_the_target_holds_the_template_and_resolution_happens_per_render(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keeping the template on the target is what lets a later render pick up a
    changed variable, and what lets `explain` show what the manifest actually says."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/opt/claude")
    root = env_root(tmp_path, "${CLAUDE_CONFIG_DIR}/CLAUDE.md")

    target = load_manifest(root / "loadout.toml").targets[0]
    assert str(target.destinations[0]) == "${CLAUDE_CONFIG_DIR}/CLAUDE.md"
    assert Path("/opt/claude/CLAUDE.md") in render_global(root)
