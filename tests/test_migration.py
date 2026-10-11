from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from loadout.discovery import discover, entry_state, live_roots
from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.migration import plan_migration
from loadout.migration_models import MigrationPlan

pytestmark = pytest.mark.integration


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
    assert {issue.code for issue in inventory.issues} == {"scope-required", "agents-required"}
    assert not plan_migration(inventory).complete
    assert inventory.agents == ()


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
    assert plan.generated_writes == ()
    retained = {entry.path for entry in plan.originals if entry.action == "retain"}
    assert tmp_path / "src/AGENTS.md" in retained
    assert tmp_path / ".claude/skills/a/SKILL.md" not in retained
    read = {candidate.path for candidate in plan.inventory.candidates}
    assert read.isdisjoint(
        {tmp_path / ".claude/rules/python.md", tmp_path / ".claude/commands/check.md"}
    )
    assert {w.path.name for w in plan.source_writes} >= {
        "config.toml",
        "permissions.toml",
        "main.md",
    }
    source = tmp_path / "loadout"
    written = {w.path.relative_to(source).as_posix() for w in plan.source_writes}
    assert {"skills/a/SKILL.md", "skills/a/scripts/check.sh"} <= written
    assert not any(path.count("/") == 1 and path.startswith("skills/") for path in written)


def test_project_init_leaves_commands_and_agents_in_place(tmp_path: Path) -> None:
    command = write(tmp_path, ".claude/commands/ship.md", "ship\n")
    agent = write(tmp_path, ".claude/agents/auditor.md", "audit\n")
    plan = migration(tmp_path, ("claude",))
    assert plan.generated_writes == ()
    assert {c.path for c in plan.inventory.candidates}.isdisjoint({command, agent})
    assert command.read_text() == "ship\n"
    assert agent.read_text() == "audit\n"
    assert "/.claude/commands" not in plan.ignores
    assert "/.claude/agents" not in plan.ignores
    assert all("commands" not in write.path.parts for write in plan.source_writes)
    assert all("agents" not in write.path.parts for write in plan.source_writes)


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
    skill = write(tmp_path, ".claude/skills/s/SKILL.md", "# s\n")
    personal = write(tmp_path, ".claude/skills/s/notes.local.md", "personal\n")
    auth = write(tmp_path, ".claude/skills/s/auth.json", secret)
    plan = migration(tmp_path)
    assert {mcp, personal, auth} <= set(plan.private_paths)
    assert skill not in set(plan.private_paths)
    assert set(plan.checkpoint_paths).isdisjoint({mcp, personal, auth})
    assert secret not in json.dumps(plan.preview())
    assert all(w.private for w in plan.source_writes if secret.encode() in w.content)
    assert all(c.content == b"" for c in plan.inventory.candidates if c.path == auth)
    assert auth.read_text() == secret


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
    assert any(m.source == Path("/isolated/home/.factory") for m in roots)
    relocated = live_roots(Path("/isolated/home"), {"FACTORY_HOME_OVERRIDE": "~/factory-home"})
    assert any(m.source == Path("/isolated/home/factory-home/.factory") for m in relocated)
    assert any(m.source == Path("/isolated/home/xdg/mcp/mcp.json") for m in roots)


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
def test_unmanaged_entries_under_a_harness_root_are_never_read(tmp_path: Path, name: str) -> None:
    path = write(tmp_path, f".claude/{name}", "unknown authored content")
    plan = migration(tmp_path)
    assert path not in {c.path for c in plan.inventory.candidates}
    assert path.read_text() == "unknown authored content"


def test_jsonc_at_a_managed_path_blocks_complete_migration(tmp_path: Path) -> None:
    path = write(tmp_path, "opencode.jsonc", "{ // comment\n}\n")
    plan = plan_migration(discover(tmp_path, scope="project", agents=("opencode",)))
    assert not plan.complete
    assert [(i.code, i.paths) for i in plan.issues] == [("unadoptable-format", (path,))]


def test_a_template_file_inside_a_skill_is_adopted_as_written(tmp_path: Path) -> None:
    write(tmp_path, ".claude/skills/s/SKILL.md", "---\nname: s\ndescription: d\n---\nx\n")
    write(tmp_path, ".claude/skills/s/config.json.tmpl", "{{ .value }}\n")
    plan = migration(tmp_path)
    written = {w.path: w.content for w in plan.source_writes}
    assert written[tmp_path / "loadout/skills/s/config.json.tmpl"] == b"{{ .value }}\n"


def test_existing_source_is_not_reinterpreted(tmp_path: Path) -> None:
    config = write(tmp_path, "loadout/config.toml", 'harnesses = ["claude"]\n')
    write(tmp_path, ".claude/new-unknown-file", "outside managed source")
    plan = plan_migration(discover(tmp_path))
    assert plan.complete
    assert plan.already_initialized
    assert plan.inventory.initialized == config
    assert plan.source_writes == ()


def materialize_sources(plan: MigrationPlan, root: Path, *, include_private: bool = True) -> None:
    for source in plan.source_writes:
        if include_private or not source.private:
            write(
                root / "loadout",
                source.path.relative_to(plan.inventory.source_root).as_posix(),
                source.content,
                source.mode,
            )


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


@pytest.mark.parametrize(
    "command",
    [
        "sh /unmapped/private-helper.sh",
        "sh $UNSET_TOOLS/helper.sh",
        "sh /unmapped/*.sh",
        "sh {root}/tools",
    ],
)
def test_an_unresolvable_dependency_is_left_in_place(tmp_path: Path, command: str) -> None:
    (tmp_path / "tools").mkdir()
    command = command.format(root=tmp_path)
    document = json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command}]}]}}
    )
    settings = write(tmp_path, ".claude/settings.json", document)
    plan = migration(tmp_path)
    assert settings in {c.path for c in plan.inventory.candidates}
    assert settings.read_text() == document


def test_directory_precondition_detects_a_new_authored_input(tmp_path: Path) -> None:
    write(tmp_path, ".claude/skills/first/SKILL.md", "first\n")
    plan = migration(tmp_path)
    state = next(s for s in plan.preconditions if s.path == tmp_path / ".claude/skills")
    write(tmp_path, ".claude/skills/second/SKILL.md", "second\n")
    assert entry_state(state.path) != state


def test_scaffold_gitkeep_remains_a_retained_dependency(tmp_path: Path) -> None:
    marker = write(tmp_path, ".claude/skills/.gitkeep", "")
    plan = migration(tmp_path)
    assert plan.expected_outputs == ()
    assert (
        next(c.disposition for c in plan.inventory.candidates if c.path == marker)
        == "retained dependency"
    )


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


def test_the_source_directory_is_never_scanned_for_harness_layouts(
    tmp_path: Path, fake_home: Path
) -> None:
    for relative in (
        ".pi/agent/settings.json",
        "claude/settings.json",
        ".claude/CLAUDE.md",
        ".gemini/settings.json",
        "install.sh",
        "Makefile",
        "settings.json",
        "CLAUDE.md",
    ):
        write(tmp_path, relative, "{}\n")
    live = write(fake_home, ".claude/CLAUDE.md", "Live rules.\n")
    plan = plan_migration(discover(tmp_path, scope="global", agents=("claude",)))
    assert plan.complete, plan.preview()
    assert {c.path for c in plan.inventory.candidates} == {live}


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
    namespace = tmp_path / "loadout/skills/one.local"
    assert namespace in plan.private_paths
    assert "/loadout/skills/one.local/" in plan.ignores
    materialize_sources(plan, tmp_path)
    added = write(namespace, "scripts/future.sh", "private future support")
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


def test_relative_dependencies_are_adopted_at_their_deployed_layout(fake_home: Path) -> None:
    script = write(fake_home, ".claude/hooks/check.sh", "source ../scripts/common.sh\n")
    helper = write(fake_home, ".claude/scripts/common.sh", "true\n")
    source = fake_home / "agent-config"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude",)))
    assert plan.complete, plan.preview()
    assert generated(plan, script) == script.read_bytes()
    assert generated(plan, helper) == helper.read_bytes()


@pytest.mark.parametrize("declaration", ["settings", "hook"])
def test_absolute_dependency_on_a_de_linked_original_stays_blocked(
    tmp_path: Path, fake_home: Path, declaration: str
) -> None:
    dotfiles = tmp_path / "dotfiles"
    helper = write(dotfiles, "scripts/common.sh", "true\n")
    if declaration == "settings":
        write(dotfiles, "settings.json", json.dumps({"command": f"sh {helper}"}))
    else:
        write(dotfiles, "hooks/check.sh", f"source {helper}\n")
    (fake_home / ".claude").symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert not plan.complete


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


def test_a_project_under_a_symlinked_prefix_is_not_external(tmp_path: Path) -> None:
    """The macOS /tmp case: the project is spelled through a link above it, so its
    canonical path never started with the spelling the boundary was given."""
    write(tmp_path, "real/project/.claude/skills/one/SKILL.md", "---\nname: one\n---\nx\n")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path / "real", target_is_directory=True)
    project = alias / "project"
    plan = plan_migration(discover(project, scope="project", agents=("claude",)))
    assert plan.complete, plan.preview()
    assert project / "loadout/skills/one/SKILL.md" in {w.path for w in plan.source_writes}


def test_symlink_cycle_in_harness_root_is_reported(tmp_path: Path) -> None:
    (tmp_path / ".claude").symlink_to(".claude", target_is_directory=True)
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"symlink-target"}


def test_private_and_runtime_originals_have_retained_dispositions(fake_home: Path) -> None:
    write(fake_home, ".claude/skills/s/SKILL.md", "---\nname: s\n---\nx\n")
    private = write(fake_home, ".claude/skills/s/notes.local.md", "personal\n")
    auth = write(fake_home, ".claude/skills/s/auth.json", "runtime-credential")
    marker = write(fake_home, ".claude/skills/.gitkeep", "")
    mixed = write(fake_home, ".codex/config.toml", 'model="test"\n[trust]\nvalue=true\n')
    source = fake_home / "agent-config"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude", "codex")))
    assert plan.complete, plan.preview()
    retained = {item.path for item in plan.originals if item.action == "retain"}
    assert {private, auth, marker, mixed} <= retained
    assert plan.obsolete == ()
    assert plan.checkpoint_paths == ()
    assert {private, auth, mixed} <= set(plan.private_paths)


@pytest.mark.parametrize("source_alias", [False, True])
def test_symlinked_harness_absolute_dependency_on_retired_source_is_blocked(
    tmp_path: Path, fake_home: Path, source_alias: bool
) -> None:
    dotfiles = tmp_path / "dotfiles"
    helper = write(dotfiles, "canonical/scripts/common.sh", "true\n")
    reference = helper
    if source_alias:
        alias = tmp_path / "alias"
        alias.symlink_to(dotfiles, target_is_directory=True)
        reference = alias / "canonical/scripts/common.sh"
    write(dotfiles, "canonical/hooks/check.sh", f"source {reference}\n")
    (fake_home / ".claude").symlink_to(dotfiles / "canonical", target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert plan.issues[0].paths == (
        fake_home / ".claude/hooks/check.sh",
        fake_home / ".claude/scripts/common.sh",
    )
    assert not plan.complete


@pytest.fixture(params=["home", "relocated"])
def linked_harness_root(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> tuple[Path, Path]:
    source = tmp_path / "source"
    source.mkdir()
    destination = fake_home / ".claude"
    if request.param == "relocated":
        destination = tmp_path / "deployment/claude"
        destination.parent.mkdir()
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(destination))
    destination.symlink_to(source, target_is_directory=True)
    return source, destination


@pytest.mark.parametrize("source_spelling", ["canonical", "source-alias", "home-source-alias"])
def test_direct_harness_root_absolute_source_dependencies_are_blocked(
    tmp_path: Path,
    fake_home: Path,
    linked_harness_root: tuple[Path, Path],
    source_spelling: str,
) -> None:
    target, destination = linked_harness_root
    helper = write(target, "scripts/common.sh", "true\n")
    reference = helper
    if source_spelling != "canonical":
        alias = (fake_home if source_spelling == "home-source-alias" else tmp_path) / "stale"
        alias.symlink_to(target, target_is_directory=True)
        reference = alias / "scripts/common.sh"
    write(target, "hooks/check.sh", f"source {reference}\n")
    source = tmp_path / "agent-config"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude",)))
    assert {issue.code for issue in plan.issues} == {"dependency-activation"}
    assert {issue.paths[0] for issue in plan.issues} == {destination / "hooks/check.sh"}
    assert not plan.complete


def test_one_skill_name_with_two_contents_refuses_without_a_selection(fake_home: Path) -> None:
    write(fake_home, ".claude/skills/review/SKILL.md", "---\nname: review\n---\nClaude.\n")
    write(fake_home, ".codex/skills/review/SKILL.md", "---\nname: review\n---\nCodex.\n")
    source = fake_home / "agent-config"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude", "codex")))
    assert [issue.code for issue in plan.issues] == ["source-composition"]
    assert "skills/review/SKILL.md" in plan.issues[0].message


def test_different_project_instruction_documents_stop_init(tmp_path: Path) -> None:
    write(tmp_path, "CLAUDE.md", "Claude rules.\n")
    write(tmp_path, "AGENTS.md", "Agents rules.\n")
    inventory = discover(tmp_path, scope="project", agents=("claude", "codex"))
    with pytest.raises(LoadoutError, match="project instruction documents disagree"):
        plan_migration(inventory)


def test_different_global_instruction_documents_stay_per_harness(fake_home: Path) -> None:
    write(fake_home, ".claude/CLAUDE.md", "Claude global.\n")
    write(fake_home, ".codex/AGENTS.md", "Codex global.\n")
    source = fake_home / "agent-config"
    source.mkdir()
    plan = plan_migration(discover(source, scope="global", agents=("claude", "codex")))
    written = {w.path.relative_to(source).as_posix(): w.content for w in plan.source_writes}
    assert written["loadout/instructions/claude.md"] == b"Claude global.\n"
    assert written["loadout/instructions/codex.md"] == b"Codex global.\n"
