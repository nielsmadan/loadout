from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from loadout.artifacts import CATEGORIES
from loadout.discovery import discover, entry_state, live_roots
from loadout.emit import Copied, Merged, render_all
from loadout.migration import plan_migration
from loadout.migration_models import MigrationPlan, RootMapping, SourceSelection
from loadout.migration_validation import validate_plan
from loadout.native_documents import parse_document
from loadout.permissions.renderers import render_codex_project, render_opencode
from loadout.permissions.rules import Rules


def write(root: Path, relative: str, content: str | bytes, mode: int = 0o644) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    path.chmod(mode)
    return path


def migration(root: Path, agents: tuple[str, ...] = ("claude",)) -> MigrationPlan:
    plan = plan_migration(discover(root, scope="project", agents=agents))
    assert plan.complete, plan.preview()
    return plan


def generated(plan: MigrationPlan, path: Path) -> bytes:
    return next(write.content for write in plan.generated_writes if write.path == path)


def index(plan: MigrationPlan) -> dict[str, object]:
    source = next(w for w in plan.source_writes if w.path.name == "artifacts.toml")
    return tomllib.loads(source.content.decode())


def test_shared_instructions_do_not_invent_scope_or_harnesses(tmp_path: Path) -> None:
    write(tmp_path, "AGENTS.md", "Shared rules\n")
    inventory = discover(tmp_path)
    assert {issue.code for issue in inventory.issues} == {
        "scope-required",
        "agents-required",
        "source-mapping",
    }
    assert not plan_migration(inventory).complete
    assert inventory.agents == ()


def test_project_plan_reconstructs_composite_native_data_without_mutation(tmp_path: Path) -> None:
    original = '{"model": null, "permissions":{"allow":["Read(a)", "Bash(git status)", "Read(b)"]}, "hooks":{"Stop":[]}, "enabledPlugins":{"x@y":false}}'
    settings = write(tmp_path, ".claude/settings.json", original)
    plan = migration(tmp_path)
    assert settings.read_text() == original
    assert (tmp_path / "loadout").exists() is False
    assert parse_document(generated(plan, settings).decode(), "json") == json.loads(original)
    assert settings in plan.checkpoint_paths
    assert all(not w.private for w in plan.source_writes)
    source_categories = {
        w.path.relative_to(plan.inventory.source_root).parts[0] for w in plan.source_writes
    }
    assert source_categories >= CATEGORIES
    assert any(
        c.category == "module-config" and c.state == "explicit-binding" for c in plan.categories
    )
    with pytest.raises(FrozenInstanceError):
        plan.validated = False  # type: ignore[misc]


def test_permission_extraction_is_verified_after_serialization(tmp_path: Path) -> None:
    content = json.dumps(render_opencode(Rules(allow=("git",)), {}))
    path = write(tmp_path, "opencode.json", content)
    plan = migration(tmp_path, ("opencode",))
    assert json.loads(generated(plan, path)) == json.loads(content)
    assert any('renderer = "opencode"' in w.content.decode() for w in plan.source_writes)


@pytest.mark.parametrize(
    "path,agents,content",
    [
        (
            ".codex/rules/custom.rules",
            ("codex",),
            'prefix_rule(pattern=["git"],decision="allow")\n',
        ),
        ("opencode.json", ("opencode",), '{"permission":{"bash":{"*":"ask","git":"allow"}}}'),
        (
            ".pi/extensions/pi-permission-system/config.json",
            ("pi",),
            '{"permission":{"mcp":{"a_b":"allow","a:b":"deny"}}}',
        ),
        (
            ".claude/settings.json",
            ("claude",),
            '{"permissions":{"allow":["Read(x)","Bash(git status)","Read(y)"]}}',
        ),
    ],
)
def test_silent_extractor_losses_use_native_fallback(
    tmp_path: Path, path: str, agents: tuple[str, ...], content: str
) -> None:
    destination = write(tmp_path, path, content)
    plan = migration(tmp_path, agents)
    output = generated(plan, destination)
    if destination.suffix == ".rules":
        assert output == content.encode()
    else:
        assert json.loads(output) == json.loads(content)
    assert "renderer" not in next(
        w.content.decode() for w in plan.source_writes if w.path.name == "artifacts.toml"
    )


def test_exact_codex_renderer_output_can_be_portable(tmp_path: Path) -> None:
    content = render_codex_project(Rules(allow=("git status",)))
    path = write(tmp_path, ".codex/rules/permissions.rules", content, 0o600)
    plan = migration(tmp_path, ("codex",))
    assert generated(plan, path) == content.encode()
    assert 'renderer = "codex-project"' in next(
        w.content.decode() for w in plan.source_writes if w.path.name == "artifacts.toml"
    )


def test_nested_instructions_and_program_dependency_layout(tmp_path: Path) -> None:
    paths = {
        "AGENTS.md": (b"shared\n", 0o644),
        "src/AGENTS.md": (b"nested\n", 0o644),
        ".claude/rules/python.md": (b"native rule\n", 0o644),
        ".claude/commands/check.md": (b"run scripts/check.sh\n", 0o644),
        ".claude/skills/a/SKILL.md": (b"# a\n", 0o644),
        ".claude/skills/a/scripts/check.sh": (b"#!/bin/sh\n. ../lib/shared.sh\n", 0o755),
        ".claude/skills/a/lib/shared.sh": (b"true\n", 0o644),
        ".claude/skills/a/fixtures/projects/sample.dat": (b"\x00\xff", 0o640),
    }
    for path, (content, mode) in paths.items():
        write(tmp_path, path, content, mode)
    plan = migration(tmp_path, ("claude", "codex"))
    assert {
        w.path.relative_to(tmp_path).as_posix(): (w.content, w.mode) for w in plan.generated_writes
    } == paths


@pytest.mark.parametrize("parent", ["projects", "cache", "logs"])
def test_workspace_parent_names_are_not_runtime_roles(tmp_path: Path, parent: str) -> None:
    root = tmp_path / parent / "configuration"
    path = write(root, ".claude/settings.json", '{"model":"test"}')
    plan = migration(root)
    assert json.loads(generated(plan, path)) == {"model": "test"}


def test_tracked_nested_instructions_survive_build_walk_filters(tmp_path: Path) -> None:
    write(tmp_path, "vendor/sub/AGENTS.md", "tracked vendor instructions\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", "vendor/sub/AGENTS.md"], check=True)
    plan = migration(tmp_path, ("codex",))
    assert generated(plan, tmp_path / "vendor/sub/AGENTS.md") == b"tracked vendor instructions\n"


def test_credentials_and_personal_sources_are_excluded_from_checkpoints(tmp_path: Path) -> None:
    secret = "literal-test-token-123"
    mcp = write(
        tmp_path,
        ".mcp.json",
        json.dumps(
            {
                "mcpServers": {
                    "http": {"headers": {"Authorization": f"Bearer {secret}"}},
                    "stdio": {"env": {"CUSTOM": secret}},
                }
            }
        ),
    )
    personal = write(tmp_path, ".claude/settings.local.json", '{"model":"personal"}')
    auth = write(tmp_path, ".claude/auth.json", secret)
    plan = migration(tmp_path)
    assert {mcp, personal, auth} <= set(plan.private_paths)
    assert set(plan.checkpoint_paths).isdisjoint({mcp, personal, auth})
    assert secret not in json.dumps(plan.preview())
    assert all(w.private for w in plan.source_writes if secret.encode() in w.content)
    assert all(c.content == b"" for c in plan.inventory.candidates if c.path == auth)
    assert auth.read_text() == secret


def test_global_live_roots_honor_relocation_and_partial_runtime_ownership(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "dotfiles"
    source.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake_home / "custom-claude"))
    monkeypatch.setenv("CODEX_HOME", str(fake_home / "custom-codex"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(fake_home / "custom-pi"))
    claude = write(
        fake_home,
        "custom-claude/.claude.json",
        '{"mcpServers":{"search":{"command":"search"}},"oauthAccount":{"token":"runtime-secret"},"projects":{"x":{}}}',
    )
    codex = write(
        fake_home,
        "custom-codex/config.toml",
        'model = "test"\n[projects."/private/project"]\ntrust_level = "trusted"\n',
    )
    pi = write(
        fake_home, "custom-pi/settings.json", '{"defaultModel":"test","lastChangelogVersion":"1"}'
    )
    inventory = discover(source, scope="global", agents=("claude", "codex", "pi"))
    plan = plan_migration(inventory)
    assert plan.complete, plan.preview()
    assert json.loads(generated(plan, claude)) == {"mcpServers": {"search": {"command": "search"}}}
    assert tomllib.loads(generated(plan, codex).decode()) == {"model": "test"}
    assert json.loads(generated(plan, pi)) == {"defaultModel": "test"}
    assert "runtime-secret" not in "".join(
        w.content.decode(errors="replace") for w in plan.source_writes
    )
    assert claude.read_text().find("runtime-secret") > 0
    assert plan.inventory.source_root == source / "loadout"


def test_default_claude_mcp_is_sibling_of_default_harness_directory(
    tmp_path: Path, fake_home: Path
) -> None:
    mcp = write(fake_home, ".claude.json", '{"mcpServers": {"one":{"command":"test"}}}')
    plan = plan_migration(discover(tmp_path, scope="global", agents=("claude",)))
    assert plan.complete, plan.preview()
    assert generated(plan, mcp)
    assert fake_home / ".claude/.claude.json" not in {c.path for c in plan.inventory.candidates}


def test_live_roots_expand_using_supplied_home() -> None:
    roots = live_roots(
        Path("/isolated/home"), {"CLAUDE_CONFIG_DIR": "~/custom", "XDG_CONFIG_HOME": "~/xdg"}
    )
    assert roots[0].source == Path("/isolated/home/custom")
    assert any(m.source == Path("/isolated/home/custom/.claude.json") for m in roots)
    assert any(m.source == Path("/isolated/home/xdg/mcp/mcp.json") for m in roots)


def test_dotfile_directory_names_require_confirmed_mapping(tmp_path: Path, fake_home: Path) -> None:
    source = write(tmp_path, "claude/settings.json", '{"model":"source"}')
    inventory = discover(tmp_path, scope="global", agents=("claude",))
    assert any(c.path == source and c.disposition == "unresolved" for c in inventory.candidates)
    mapping = RootMapping(source.parent, fake_home / ".claude", ("claude",))
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert json.loads(generated(plan, fake_home / ".claude/settings.json")) == {"model": "source"}


def test_source_and_live_conflict_requires_explicit_selection(
    tmp_path: Path, fake_home: Path
) -> None:
    source = write(tmp_path, "claude/settings.json", '{"model":"source"}')
    live = write(fake_home, ".claude/settings.json", '{"model":"live"}')
    inventory = discover(
        tmp_path,
        scope="global",
        agents=("claude",),
        mappings=(RootMapping(source.parent, live.parent, ("claude",)),),
    )
    unresolved = plan_migration(inventory)
    assert {i.code for i in unresolved.issues} == {"source-conflict"}
    selected = plan_migration(inventory, selections=(SourceSelection(live, source),))
    assert selected.complete, selected.preview()
    assert json.loads(generated(selected, live)) == {"model": "source"}
    assert live.read_text() == '{"model":"live"}'


def test_symlink_external_target_requires_mapping_and_is_never_written(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = write(tmp_path, "shared/CLAUDE.md", "external instructions\n")
    link = project / "CLAUDE.md"
    link.symlink_to(outside)
    inventory = discover(project, scope="project", agents=("claude",))
    assert {i.code for i in inventory.issues} == {"external-symlink"}
    plan = plan_migration(
        discover(
            project,
            scope="project",
            agents=("claude",),
            mappings=(RootMapping(outside, link, ("claude",), "file", "instructions"),),
        )
    )
    assert plan.complete, plan.preview()
    assert generated(plan, link) == b"external instructions\n"
    assert link.is_symlink()
    assert outside.read_text() == "external instructions\n"
    assert any(s.path == link and s.link == str(outside) for s in plan.preconditions)
    assert any(s.path == outside and s.digest for s in plan.preconditions)
    assert outside not in plan.obsolete
    retained = next(item for item in plan.originals if item.path == outside)
    assert retained.action == "retain"
    assert retained.reason == "External original remains outside the retirement boundary."


def test_symlink_into_known_runtime_root_is_excluded(tmp_path: Path) -> None:
    auth = write(tmp_path, ".claude/auth.json", "never read this credential\n")
    link = tmp_path / ".claude/skills/probe/SKILL.md"
    link.parent.mkdir(parents=True)
    link.symlink_to(auth)
    inventory = discover(tmp_path, scope="project", agents=("claude",))
    assert all(c.disposition == "runtime-private exclusion" for c in inventory.candidates)
    assert all(c.content == b"" for c in inventory.candidates)


@pytest.mark.parametrize(
    "name", ["preferences.md", "unrecognized.data", "opencode.jsonc", "config.json.tmpl"]
)
def test_unknown_native_and_materialization_inputs_block_complete_migration(
    tmp_path: Path, name: str
) -> None:
    path = write(tmp_path, f".claude/{name}", "unknown authored content")
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert not plan.complete
    assert any(c.path == path and c.disposition == "unresolved" for c in plan.inventory.candidates)


def test_existing_source_is_not_reinterpreted(tmp_path: Path) -> None:
    config = write(tmp_path, "loadout/config.toml", 'harnesses = ["claude"]\n')
    write(tmp_path, ".claude/new-unknown-file", "outside managed source")
    plan = plan_migration(discover(tmp_path))
    assert plan.complete
    assert plan.already_initialized
    assert plan.inventory.initialized == config
    assert plan.source_writes == ()


def test_validation_rejects_actual_serialized_source_changes(tmp_path: Path) -> None:
    path = write(tmp_path, ".claude/settings.json", '{"model":"original"}')
    plan = migration(tmp_path)
    sources = tuple(
        replace(w, content=w.content.replace(b'"original"', b'"mutated"'))
        for w in plan.source_writes
    )
    assert any(b'"mutated"' in w.content for w in sources)
    changed = validate_plan(replace(plan, source_writes=sources))
    assert not changed.complete
    assert changed.issues[0].code == "reconstruction-failed"
    assert path.read_text() == '{"model":"original"}'


def test_empty_routes_do_not_suppress_fallback_instructions(tmp_path: Path) -> None:
    plan = migration(tmp_path, ("claude", "codex", "opencode", "pi"))
    assert {o.path for o in plan.expected_outputs} == set()
    assert {tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md"} <= set(plan.required_absences)
    assert {c.category for c in plan.categories if c.state == "routed"} >= {
        "settings",
        "instructions",
        "permissions",
        "hooks",
        "plugins",
        "skills",
        "mcp",
    }


def test_originally_empty_instruction_file_is_recreated(tmp_path: Path) -> None:
    path = write(tmp_path, "CLAUDE.md", b"")
    plan = migration(tmp_path)
    assert generated(plan, path) == b""


def test_global_synthetic_dbochman_and_anaiis_shapes(tmp_path: Path, fake_home: Path) -> None:
    files = {
        "claude/agents/reviewer.md": "review\n",
        "claude/commands/check.md": "check\n",
        "claude/hooks/check.sh": "#!/bin/sh\ntrue\n",
        "claude/scripts/helper.sh": "true\n",
        "claude/rules/python.md": "Python rules\n",
        "claude/keybindings.json": '{"bindings":[]}',
        "claude/rtk-filters.toml": "enabled = false\n",
        "codex/profiles/review.config.toml": 'model = "review"\n',
        "codex/rules/default.rules": 'prefix_rule(pattern=["git"],decision="allow")\n',
        "codex/AGENTS.md": "Codex rules\n",
        "opencode/plugin/tmux-notify.js": "export default {}\n",
    }
    for name, content in files.items():
        write(tmp_path, name, content, 0o755 if name.endswith(".sh") else 0o644)
    mappings = tuple(
        RootMapping(tmp_path / name, fake_home / relative, (name,))
        for name, relative in (
            ("claude", ".claude"),
            ("codex", ".codex"),
            ("opencode", ".config/opencode"),
        )
    )
    plan = plan_migration(
        discover(
            tmp_path, scope="global", agents=("claude", "codex", "opencode"), mappings=mappings
        )
    )
    assert plan.complete, plan.preview()
    assert len(plan.expected_outputs) == len(files)
    shell = next(w for w in plan.generated_writes if w.path == fake_home / ".claude/hooks/check.sh")
    assert shell.mode == 0o755


def materialize_sources(plan: MigrationPlan, root: Path, *, include_private: bool = True) -> None:
    for source in plan.source_writes:
        if include_private or not source.private:
            write(
                root / "loadout",
                source.path.relative_to(plan.inventory.source_root).as_posix(),
                source.content,
                source.mode,
            )


@pytest.mark.parametrize(
    "agent,category,content",
    [
        ("claude", "permissions", {"permissions": {"allow": ["Bash(git status)"]}}),
        ("claude", "hooks", {"hooks": {"Stop": []}}),
        ("claude", "plugins", {"enabledPlugins": {"example@market": False}}),
        ("claude", "mcp", {"mcpServers": {"test": {"command": "server"}}}),
        ("opencode", "permissions", {"permission": {"bash": {"*": "ask"}}}),
        ("opencode", "mcp", {"mcp": {"test": {"type": "local", "command": ["server"]}}}),
        ("pi", "plugins", {"packages": ["npm:example"]}),
    ],
)
def test_dormant_document_parts_activate_on_first_entry(
    tmp_path: Path, agent: str, category: str, content: dict[str, object]
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    plan = migration(origin, (agent,))
    fresh = tmp_path / "fresh"
    materialize_sources(plan, fresh)
    records = tomllib.loads((fresh / "loadout/artifacts.toml").read_text())["artifact"]
    record = next(r for r in records if category in r.get("parts", {}))
    source = fresh / "loadout" / record["parts"][category]["source"]
    source.write_text(json.dumps(content))
    outputs = render_all(fresh)
    output = outputs[fresh / record["output"]]
    actual = output.document if isinstance(output, Merged) else output
    assert isinstance(actual, str)
    assert json.loads(actual) == content


def test_skill_tree_first_entry_edit_rename_and_delete(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    plan = migration(origin)
    fresh = tmp_path / "fresh"
    materialize_sources(plan, fresh)
    record = next(r for r in index(plan)["artifact"] if r.get("category") == "skills")
    source_root = fresh / "loadout" / record["source"]
    path = write(source_root, "first/SKILL.md", "first skill\n")
    output = render_all(fresh)[fresh / ".claude/skills/first/SKILL.md"]
    assert isinstance(output, Copied)
    assert output.source.read_text() == "first skill\n"
    path.write_text("changed skill\n")
    renamed = path.with_name("changed.md")
    path.rename(renamed)
    outputs = render_all(fresh)
    assert tuple(outputs) == (fresh / ".claude/skills/first/changed.md",)
    renamed.unlink()
    assert render_all(fresh) == {}


def test_partial_output_mode_policy_preserves_adopted_destination_mode(tmp_path: Path) -> None:
    path = write(tmp_path, ".codex/config.toml", 'model = "test"\n', 0o640)
    plan = migration(tmp_path, ("codex",))
    output = next(w for w in plan.generated_writes if w.path == path)
    assert (output.mode, output.mode_policy) == (0o640, "preserve-destination")
    settings = write(tmp_path, ".claude/settings.json", '{"model":"test"}')
    other = migration(tmp_path, ("claude",))
    assert next(w.mode for w in other.generated_writes if w.path == settings) == 0o600


@pytest.mark.parametrize(
    "agent,path,content",
    [
        ("codex", ".codex/config.toml", 'model = "personal"\n'),
        ("pi", ".pi/settings.json", '{"defaultModel":"personal"}'),
    ],
)
def test_gitignored_authored_configuration_remains_personal(
    tmp_path: Path, agent: str, path: str, content: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, ".gitignore", path + "\n")
    original = write(tmp_path, path, content)
    plan = migration(tmp_path, (agent,))
    assert original in plan.private_paths
    assert original not in plan.checkpoint_paths
    assert all(w.private for w in plan.source_writes if b"personal" in w.content)


def test_ignored_symlink_output_keeps_tracked_canonical_source_public(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    write(tmp_path, ".gitignore", "CLAUDE.md\n")
    source = write(tmp_path, "authoring/shared.md", "public instruction\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", "authoring/shared.md"], check=True)
    (tmp_path / "CLAUDE.md").symlink_to(source)
    plan = migration(tmp_path)
    assert all(not w.private for w in plan.source_writes)


def test_private_routes_are_optional_in_a_checkout_without_personal_files(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    write(origin, ".claude/settings.local.json", '{"model":"private-model"}')
    plan = migration(origin)
    fresh = tmp_path / "fresh"
    materialize_sources(plan, fresh, include_private=False)
    outputs = render_all(fresh)
    assert all("private-model" not in str(value) for value in outputs.values())


def test_static_hook_dependency_is_inventoried_and_reconstructed(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".claude/settings.json",
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"sh ./tools/check.sh"}]}]}}',
    )
    dependency = write(tmp_path, "tools/check.sh", "#!/bin/sh\ntrue\n", 0o755)
    plan = migration(tmp_path)
    assert generated(plan, dependency) == b"#!/bin/sh\ntrue\n"
    assert any(c.path == dependency and c.category == "support" for c in plan.inventory.candidates)


def test_unmapped_external_hook_dependency_blocks_completion(tmp_path: Path) -> None:
    write(
        tmp_path,
        ".claude/settings.json",
        '{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"sh /unmapped/private-helper.sh"}]}]}}',
    )
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"dependency-mapping"}


def test_shared_agents_tree_keeps_its_actual_consumers(tmp_path: Path) -> None:
    path = write(tmp_path, ".agents/skills/shared/SKILL.md", "shared skill\n")
    plan = migration(tmp_path, ("claude", "codex", "opencode", "pi"))
    assert generated(plan, path) == b"shared skill\n"
    record = next(r for r in index(plan)["artifact"] if r["output"] == ".agents/skills")
    assert record["agents"] == ["opencode", "pi"]


def test_directory_precondition_detects_a_new_authored_input(tmp_path: Path) -> None:
    write(tmp_path, ".claude/commands/first.md", "first\n")
    plan = migration(tmp_path)
    state = next(s for s in plan.preconditions if s.path == tmp_path / ".claude/commands")
    write(tmp_path, ".claude/commands/second.md", "second\n")
    assert entry_state(state.path) != state


def test_scaffold_gitkeep_remains_a_retained_dependency(tmp_path: Path) -> None:
    marker = write(tmp_path, ".claude/skills/.gitkeep", "")
    plan = migration(tmp_path)
    assert plan.expected_outputs == ()
    assert (
        next(c.disposition for c in plan.inventory.candidates if c.path == marker)
        == "retained dependency"
    )


def test_support_json_assets_remain_opaque_and_have_no_unrelated_category_parts(
    tmp_path: Path,
) -> None:
    asset = write(tmp_path, ".claude/hooks/assets/auth.json", b'{ "fixture" : null }\n', 0o640)
    keybindings = write(tmp_path, ".claude/keybindings.json", '{"bindings":[]}')
    plan = migration(tmp_path)
    assert generated(plan, asset) == b'{ "fixture" : null }\n'
    record = next(
        r for r in index(plan)["artifact"] if r["output"] == str(keybindings.relative_to(tmp_path))
    )
    assert tuple(record["parts"]) == ("settings",)


def test_runtime_history_references_are_excluded_before_dependency_discovery(
    tmp_path: Path, fake_home: Path
) -> None:
    write(
        fake_home,
        ".claude.json",
        '{"mcpServers": {}, "projects": {"historic": {"path": "/unknown/historic-project"}}}',
    )
    plan = plan_migration(discover(tmp_path, scope="global", agents=("claude",)))
    assert plan.complete, plan.preview()


def test_explicit_pi_agent_source_does_not_leave_its_parent_layout_unresolved(
    tmp_path: Path, fake_home: Path
) -> None:
    source = write(tmp_path, ".pi/agent/settings.json", '{"defaultModel":"selected"}')
    mapping = RootMapping(source.parent, fake_home / ".pi/agent", ("pi",))
    plan = plan_migration(discover(tmp_path, scope="global", agents=("pi",), mappings=(mapping,)))
    assert plan.complete, plan.preview()


def test_opencode_supplemental_paths_are_part_of_live_inventory(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    extra = write(fake_home, "extra/config.json", '{"model":"extra"}')
    directory = fake_home / "extra-root"
    plugin = write(directory, "plugins/extra.ts", "export default {}\n")
    monkeypatch.setenv("OPENCODE_CONFIG", str(extra))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(directory))
    plan = plan_migration(discover(tmp_path, scope="global", agents=("opencode",)))
    assert plan.complete, plan.preview()
    assert {extra, plugin} <= {w.path for w in plan.generated_writes}


def test_explicit_file_mapping_resolves_unknown_native_category(tmp_path: Path) -> None:
    original = write(tmp_path, ".claude/preferences.md", "native preferences\n")
    mapping = RootMapping(original, original, ("claude",), "file", "instructions")
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert generated(plan, original) == b"native preferences\n"


def test_symlinked_harness_root_excludes_canonical_runtime_aliases(tmp_path: Path) -> None:
    auth = write(tmp_path, "canonical/auth.json", "opaque-runtime-credential")
    alias = tmp_path / "canonical/skills/one/SKILL.md"
    alias.parent.mkdir(parents=True)
    alias.symlink_to("../../auth.json")
    (tmp_path / ".claude").symlink_to("canonical", target_is_directory=True)
    plan = migration(tmp_path)
    candidates = {c.path: c for c in plan.inventory.candidates}
    assert candidates[tmp_path / ".claude/skills/one/SKILL.md"].disposition == (
        "runtime-private exclusion"
    )
    assert candidates[tmp_path / ".claude/skills/one/SKILL.md"].content == b""
    assert auth in plan.private_paths
    assert plan.generated_writes == ()


def test_private_instruction_excludes_canonical_original_and_aliases(tmp_path: Path) -> None:
    canonical = write(tmp_path, "authoring/private.md", "api_key=literal-review-secret\n")
    for name in ("CLAUDE.md", "AGENTS.md"):
        (tmp_path / name).symlink_to(canonical)
    plan = migration(tmp_path, ("claude", "codex"))
    originals = {canonical, tmp_path / "CLAUDE.md", tmp_path / "AGENTS.md"}
    assert originals <= set(plan.private_paths)
    assert {"/" + p.relative_to(tmp_path).as_posix() for p in originals} <= set(plan.ignores)
    assert set(plan.checkpoint_paths).isdisjoint(originals)
    assert set(plan.obsolete).isdisjoint(originals)


def test_private_tree_namespace_ignores_future_entries(tmp_path: Path) -> None:
    write(tmp_path, ".claude/skills/one/SKILL.md", "api_key=literal-review-secret\n")
    plan = migration(tmp_path)
    record = next(r for r in index(plan)["artifact"] if r.get("category") == "skills")
    assert record["optional"] is True
    namespace = tmp_path / "loadout/skills/local"
    assert namespace in plan.private_paths
    assert "/loadout/skills/local/" in plan.ignores
    materialize_sources(plan, tmp_path)
    added = write(tmp_path / "loadout" / record["source"], "two/SKILL.md", "private future skill")
    write(tmp_path, ".gitignore", "\n".join(plan.ignores) + "\n")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "--", str(added)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(added)


def test_mapped_tree_retirement_accounts_for_every_original(
    tmp_path: Path, fake_home: Path
) -> None:
    files = {
        write(tmp_path, "claude/commands/foo.md", "command\n"),
        write(tmp_path, "claude/hooks/check.sh", "true\n"),
        write(tmp_path, "claude/skills/one/SKILL.md", "skill\n"),
    }
    mapping = RootMapping(tmp_path / "claude", fake_home / ".claude", ("claude",))
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert set(plan.obsolete) == files
    assert {item["path"] for item in plan.preview()["originals"] if item["action"] == "retire"} == {
        str(path) for path in files
    }


def test_duplicate_and_symlink_sources_have_explicit_retirement(
    tmp_path: Path, fake_home: Path
) -> None:
    first = write(tmp_path, "claude/commands/foo.md", "first\n")
    second = write(tmp_path, "alternate/commands/foo.md", "second\n")
    canonical = write(tmp_path, "authored/skill.md", "skill\n")
    alias = tmp_path / "claude/skills/one/SKILL.md"
    alias.parent.mkdir(parents=True)
    alias.symlink_to(canonical)
    mappings = tuple(
        RootMapping(tmp_path / name, fake_home / ".claude", ("claude",))
        for name in ("claude", "alternate")
    )
    destination = fake_home / ".claude/commands/foo.md"
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude",), mappings=mappings),
        selections=(SourceSelection(destination, first),),
    )
    assert plan.complete, plan.preview()
    assert set(plan.obsolete) == {first, second, alias, canonical}
    assert generated(plan, destination) == b"first\n"


def test_ancestor_spelling_does_not_retire_a_generated_destination(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    original = write(physical, ".claude/commands/one.md", "command\n")
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    logical = alias / ".claude/commands/one.md"
    plan = migration(alias)
    assert generated(plan, logical) == original.read_bytes()
    assert plan.obsolete == ()


def test_mapped_relative_dependencies_preserve_source_and_deployed_layout(
    tmp_path: Path, fake_home: Path
) -> None:
    script = write(tmp_path, "claude/hooks/check.sh", "source ../scripts/common.sh\n")
    helper = write(tmp_path, "claude/scripts/common.sh", "true\n")
    mapping = RootMapping(tmp_path / "claude", fake_home / ".claude", ("claude",))
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert generated(plan, fake_home / ".claude/hooks/check.sh") == script.read_bytes()
    assert generated(plan, fake_home / ".claude/scripts/common.sh") == helper.read_bytes()
    assert set(plan.obsolete) == {script, helper}


@pytest.mark.parametrize("declaration", ["settings", "hook"])
def test_absolute_dependency_on_relocated_original_stays_blocked(
    tmp_path: Path, fake_home: Path, declaration: str
) -> None:
    helper = write(tmp_path, "claude/scripts/common.sh", "true\n")
    if declaration == "settings":
        write(tmp_path, "claude/settings.json", json.dumps({"command": f"sh {helper}"}))
    else:
        write(tmp_path, "claude/hooks/check.sh", f"source {helper}\n")
    mapping = RootMapping(tmp_path / "claude", fake_home / ".claude", ("claude",))
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert not plan.complete


def test_global_unsupported_harness_tree_is_unresolved(tmp_path: Path, fake_home: Path) -> None:
    unsupported = write(tmp_path, ".gemini/settings.json", "{}")
    write(tmp_path, "ordinary-project/settings.json", "{}")
    plan = plan_migration(discover(tmp_path, scope="global", agents=("claude",)))
    assert not plan.complete
    assert {issue.code for issue in plan.issues} == {"unsupported-harness"}
    assert {c.path for c in plan.inventory.candidates} == {unsupported.parent}


def test_instruction_search_records_each_traversed_directory(tmp_path: Path) -> None:
    write(tmp_path, "src/code.py", "pass\n")
    plan = migration(tmp_path)
    before = {s.path: s for s in plan.preconditions}
    assert before[tmp_path / "src"].digest is not None
    instruction = write(tmp_path, "src/CLAUDE.md", "new instruction\n")
    assert entry_state(tmp_path / "src") != before[tmp_path / "src"]
    assert any(
        c.path == instruction
        for c in discover(tmp_path, scope="project", agents=("claude",)).candidates
    )


def test_instruction_search_preconditions_canonical_root_listing(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    plan = migration(alias)
    state = next(s for s in plan.preconditions if s.path == physical)
    assert state.digest is not None
    write(physical, "src/CLAUDE.md", "new nested instruction\n")
    assert entry_state(physical) != state


def test_symlinked_harness_root_does_not_expand_inventory_boundary(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = write(tmp_path, "external/skills/one/SKILL.md", "external skill\n")
    (project / ".claude").symlink_to(external.parents[2], target_is_directory=True)
    plan = plan_migration(discover(project, scope="project", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"external-symlink"}
    assert plan.source_writes == ()


def test_symlink_cycle_in_harness_root_is_reported(tmp_path: Path) -> None:
    (tmp_path / ".claude").symlink_to(".claude", target_is_directory=True)
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"symlink-target"}


def test_mapped_private_and_runtime_originals_have_retained_dispositions(
    tmp_path: Path, fake_home: Path
) -> None:
    private = write(tmp_path, "claude/settings.local.json", '{"model":"personal"}')
    auth = write(tmp_path, "claude/auth.json", "runtime-credential")
    marker = write(tmp_path, "claude/skills/.gitkeep", "")
    mixed = write(tmp_path, "codex/config.toml", 'model="test"\n[trust]\nvalue=true\n')
    mappings = (
        RootMapping(tmp_path / "claude", fake_home / ".claude", ("claude",)),
        RootMapping(tmp_path / "codex", fake_home / ".codex", ("codex",)),
    )
    plan = plan_migration(
        discover(tmp_path, scope="global", agents=("claude", "codex"), mappings=mappings)
    )
    assert plan.complete, plan.preview()
    assert {item.path for item in plan.originals if item.action == "retain"} == {
        private,
        auth,
        marker,
        mixed,
    }
    assert plan.obsolete == ()
    assert plan.checkpoint_paths == ()
    assert {private, auth, mixed} <= set(plan.private_paths)


def test_an_original_required_by_another_output_is_not_retired(tmp_path: Path) -> None:
    original = write(tmp_path, ".claude/commands/one.md", "shared command\n")
    second = tmp_path / ".claude/commands/two.md"
    mapping = RootMapping(original, second, ("claude",), "file", "instructions")
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert {output.path for output in plan.generated_writes} == {original, second}
    assert next(item.action for item in plan.originals if item.path == original) == "generated"
    assert plan.obsolete == ()


def test_symlinked_harness_directory_uses_final_retirement_topology(tmp_path: Path) -> None:
    original = write(tmp_path, "canonical/hooks/check.sh", "true\n")
    (tmp_path / ".claude").symlink_to("canonical", target_is_directory=True)
    destination = tmp_path / ".claude/hooks/check.sh"
    plan = migration(tmp_path)
    assert generated(plan, destination) == original.read_bytes()
    assert plan.obsolete == (original,)
    assert {item.path: item.action for item in plan.originals} == {
        destination: "generated",
        original: "retire",
    }


@pytest.mark.parametrize("source_alias", [False, True])
def test_symlinked_harness_absolute_dependency_on_retired_source_is_blocked(
    tmp_path: Path, source_alias: bool
) -> None:
    project = tmp_path / "project"
    helper = write(project, "canonical/scripts/common.sh", "true\n")
    reference = helper
    if source_alias:
        alias = tmp_path / "alias"
        alias.symlink_to(project, target_is_directory=True)
        reference = alias / "canonical/scripts/common.sh"
    write(project, "canonical/hooks/check.sh", f"source {reference}\n")
    (project / ".claude").symlink_to("canonical", target_is_directory=True)
    plan = plan_migration(discover(project, scope="project", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert plan.issues[0].paths == (
        project / ".claude/hooks/check.sh",
        project / ".claude/scripts/common.sh",
    )
    assert not plan.complete


@pytest.mark.parametrize("reference_kind", ["relative", "physical", "ancestor-alias"])
def test_symlinked_harness_dependencies_follow_final_destinations(
    tmp_path: Path, reference_kind: str
) -> None:
    project = tmp_path / "project"
    helper = write(project, "canonical/scripts/common.sh", "true\n")
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    references = {
        "relative": "../scripts/common.sh",
        "physical": str(project / ".claude/scripts/common.sh"),
        "ancestor-alias": str(alias / ".claude/scripts/common.sh"),
    }
    script = write(project, "canonical/hooks/check.sh", f"source {references[reference_kind]}\n")
    (project / ".claude").symlink_to("canonical", target_is_directory=True)
    plan = migration(project)
    assert generated(plan, project / ".claude/hooks/check.sh") == script.read_bytes()
    assert generated(plan, project / ".claude/scripts/common.sh") == helper.read_bytes()
    assert set(plan.obsolete) == {script, helper}


@pytest.fixture(params=["home", "relocated"])
def direct_harness_mapping(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> RootMapping:
    source = tmp_path / "source"
    source.mkdir()
    destination = fake_home / ".claude"
    if request.param == "relocated":
        destination = tmp_path / "deployment/claude"
        destination.parent.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(destination))
    destination.symlink_to(source, target_is_directory=True)
    return RootMapping(source, destination, ("claude",))


@pytest.mark.parametrize("source_spelling", ["canonical", "source-alias", "home-source-alias"])
def test_direct_harness_root_absolute_source_dependencies_are_blocked(
    tmp_path: Path,
    fake_home: Path,
    direct_harness_mapping: RootMapping,
    source_spelling: str,
) -> None:
    mapping = direct_harness_mapping
    helper = write(mapping.source, "scripts/common.sh", "true\n")
    reference = helper
    if source_spelling != "canonical":
        alias = (fake_home if source_spelling == "home-source-alias" else tmp_path) / "stale"
        alias.symlink_to(mapping.source, target_is_directory=True)
        reference = alias / "scripts/common.sh"
    write(mapping.source, "hooks/check.sh", f"source {reference}\n")
    plan = plan_migration(
        discover(mapping.source, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert {issue.paths[0] for issue in plan.issues} == {
        mapping.source / "hooks/check.sh",
        mapping.destination / "hooks/check.sh",
    }
    assert not plan.complete


@pytest.mark.parametrize("reference_kind", ["relative", "absolute", "ancestor-alias"])
def test_direct_harness_root_dependencies_keep_active_destinations(
    tmp_path: Path, direct_harness_mapping: RootMapping, reference_kind: str
) -> None:
    mapping = direct_harness_mapping
    helper = write(mapping.source, "scripts/common.sh", "true\n")
    alias = tmp_path / "active-parent"
    alias.symlink_to(mapping.destination.parent, target_is_directory=True)
    references = {
        "relative": "../scripts/common.sh",
        "absolute": str(mapping.destination / "scripts/common.sh"),
        "ancestor-alias": str(alias / mapping.destination.name / "scripts/common.sh"),
    }
    script = write(mapping.source, "hooks/check.sh", f"source {references[reference_kind]}\n")
    plan = plan_migration(
        discover(mapping.source, scope="global", agents=("claude",), mappings=(mapping,))
    )
    assert plan.complete, plan.preview()
    assert generated(plan, mapping.destination / "hooks/check.sh") == script.read_bytes()
    assert generated(plan, mapping.destination / "scripts/common.sh") == helper.read_bytes()
    assert set(plan.obsolete) == {script, helper}
    assert {item.path: item.action for item in plan.originals} == {
        mapping.destination / "hooks/check.sh": "generated",
        mapping.destination / "scripts/common.sh": "generated",
        script: "retire",
        helper: "retire",
    }


def test_relative_dependency_with_disagreeing_mapping_stays_blocked(
    tmp_path: Path, fake_home: Path
) -> None:
    write(tmp_path, "claude/hooks/check.sh", "source ../scripts/common.sh\n")
    helper = write(tmp_path, "helpers/common.sh", "true\n")
    source = tmp_path / "claude/scripts/common.sh"
    source.parent.mkdir(parents=True)
    source.symlink_to(helper)
    mappings = (
        RootMapping(
            tmp_path / "claude/hooks", fake_home / ".claude/hooks", ("claude",), category="hooks"
        ),
        RootMapping(
            source, fake_home / ".claude/elsewhere/common.sh", ("claude",), "file", "support"
        ),
    )
    plan = plan_migration(discover(tmp_path, scope="global", agents=("claude",), mappings=mappings))
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert not plan.complete
