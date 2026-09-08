from __future__ import annotations

import json
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

import pytest
import tomlkit

from loadout import artifacts, template_catalog, template_vendor
from loadout.cli import main
from loadout.deployment import DeploymentConflict
from loadout.discovery import discover
from loadout.emit import Copied, check_all, render_global, render_project, write_all
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.migration import plan_migration
from loadout.migration_transaction import apply_migration, prepare_migration
from loadout.project import load_project_config, project_config_path
from loadout.staged import check_staged
from loadout.template_catalog import load_catalog
from loadout.templates import resolve_template, template_divergence, tree_hash, vendored_path


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def catalog(fake_home: Path) -> Path:
    source = fake_home / "ac"
    catalog = source / "loadout/templates"
    _write(
        source / "loadout.toml",
        '[[source]]\nname = "ac"\npath = "loadout"\nuse = ["templates"]\n[claude]\ninstructions = []\n',
    )
    _write(machine_config_path(), f'source = "{source}"\n')
    _write(
        catalog / "nextjs.toml",
        'skills = ["review-typescript"]\ninstructions = ["typescript", "nextjs"]\nmcp = ["github"]\npermissions = ["node"]\n',
    )
    _write(
        catalog / "react-native.toml",
        'skills = ["review-typescript"]\ninstructions = ["typescript"]\nmcp = ["github"]\n',
    )
    _write(
        catalog / "skills/review-typescript/SKILL.md",
        "---\nname: review-typescript\ndescription: Review types.\n---\nReview TypeScript.\n",
    )
    _write(catalog / "instructions/typescript.md", "Shared TypeScript advice\n")
    _write(catalog / "instructions/nextjs.md", "Next.js advice\n")
    _write(catalog / "mcp/github.toml", '[github]\ntransport = "stdio"\ncommand = "github-mcp"\n')
    _write(catalog / "permissions/node.toml", '[shell]\nallow = ["npm test"]\n')
    return catalog


def _native(root: Path, agents: tuple[str, ...] = ("claude",)) -> None:
    plan = plan_migration(discover(root, scope="project", agents=agents))
    assert plan.issues == ()
    for write in plan.source_writes:
        target = root / "loadout" / write.path.relative_to(plan.inventory.source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(write.content)
        target.chmod(write.mode)


def _template(root: Path, action: str, name: str) -> int:
    return main(["template", action, name, "--root", str(root)])


def test_two_templates_share_one_vendored_part_and_render_once(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    local = bare_project / "loadout/templates"
    assert list(local.rglob("SKILL.md")) == [local / "skills/review-typescript/SKILL.md"]
    machine_config_path().unlink()
    output = render_project(bare_project)
    instructions = output[bare_project / "CLAUDE.md"]
    assert isinstance(instructions, str)
    assert instructions.count("Shared TypeScript advice") == 1
    assert "Next.js advice" in instructions
    assert (
        json.loads(output[bare_project / ".mcp.json"])["mcpServers"]["github"]["command"]
        == "github-mcp"
    )
    assert template_divergence(bare_project) == []


def test_native_fresh_project_renders_all_catalog_parts(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path, ("claude", "codex", "opencode", "pi"))
    assert _template(tmp_path, "vendor", "nextjs") == 0
    written = write_all(tmp_path)
    assert tmp_path / ".agents/skills/review-typescript/SKILL.md" in written
    assert "Shared TypeScript advice" in (tmp_path / "AGENTS.md").read_text()
    assert (
        json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]["github"]["command"]
        == "github-mcp"
    )
    assert "npm" in (tmp_path / ".codex/rules/permissions.rules").read_text()
    assert template_divergence(tmp_path) == []


def test_shared_update_prompts_for_all_affected_templates(
    bare_project: Path,
    catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    path = catalog / "instructions/typescript.md"
    path.write_text("Updated shared advice\n")
    before = project_config_path(bare_project).read_bytes()
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert _template(bare_project, "sync", "nextjs") == 1
    assert project_config_path(bare_project).read_bytes() == before
    assert "Affected templates: nextjs, react-native" in capsys.readouterr().out
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert _template(bare_project, "sync", "nextjs") == 0
    assert (
        bare_project / "loadout/templates/instructions/typescript.md"
    ).read_text() == "Updated shared advice\n"
    assert template_divergence(bare_project) == []
    config = load_project_config(project_config_path(bare_project))
    assert config.vendored_hash("react-native") == tree_hash(
        vendored_path(bare_project, "react-native")
    )


def test_shared_update_refuses_local_edits_in_other_affected_template(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    local = bare_project / "loadout/templates"
    (local / "react-native.toml").write_text('instructions = ["typescript"]\n')
    (catalog / "instructions/typescript.md").write_text("upstream\n")
    assert _template(bare_project, "sync", "nextjs") == 3
    assert (local / "instructions/typescript.md").read_text() == "Shared TypeScript advice\n"


@pytest.mark.parametrize("reference", ("../skills/global", "/tmp/skill", "nested/skill", ".."))
def test_references_cannot_escape_catalog(catalog: Path, reference: str) -> None:
    path = _write(catalog / "invalid.toml", f"skills = [{json.dumps(reference)}]\n")
    with pytest.raises(LoadoutError, match="catalog names"):
        load_catalog(path)


def test_qualified_catalog_name_vendors_valid_toml(bare_project: Path, catalog: Path) -> None:
    assert _template(bare_project, "vendor", "ac/nextjs") == 0
    config = load_project_config(project_config_path(bare_project))
    assert config.templates == ("ac/nextjs",)
    assert (
        resolve_template("ac/nextjs", bare_project).path
        == bare_project / "loadout/templates/ac/nextjs.toml"
    )


def test_commented_template_list_remains_valid(bare_project: Path, catalog: Path) -> None:
    path = project_config_path(bare_project)
    path.write_text(path.read_text() + '"templates" = [] # keep this list\n')
    assert _template(bare_project, "add", "nextjs") == 0
    assert load_project_config(path).templates == ("nextjs",)
    assert "# keep this list" in path.read_text()


def test_native_global_source_offers_catalog_without_active_source_slices(
    bare_project: Path, catalog: Path
) -> None:
    global_root = catalog.parent.parent
    _write(global_root / "loadout.toml", 'artifacts = "loadout/artifacts.toml"\n')
    _write(global_root / "loadout/artifacts.toml", "artifact = []\n")
    assert resolve_template("nextjs", bare_project).path == catalog / "nextjs.toml"


def test_catalog_parts_do_not_activate_global_outputs(catalog: Path) -> None:
    root = catalog.parent.parent
    _write(
        root / "loadout.toml",
        '[[source]]\nname = "ac"\npath = "loadout"\n[claude]\ninstructions = []\n',
    )
    _write(catalog.parent / "permissions.toml", "")
    _write(catalog.parent / "skills/active/SKILL.md", "---\nname: active\n---\nActive skill\n")
    parked = catalog.parents[2] / "parked-catalog"
    catalog.rename(parked)
    before = render_global(root)
    assert "Active skill" in before[catalog.parents[2] / ".claude/skills/active/SKILL.md"]
    parked.rename(catalog)
    assert render_global(root) == before
    _write(catalog / "skills/review-typescript/SKILL.md", "Changed referenced skill")
    _write(catalog / "instructions/typescript.md", "Changed referenced instructions")
    _write(catalog / "mcp/github.toml", '[github]\ntransport="stdio"\ncommand="changed"\n')
    _write(catalog / "permissions/node.toml", '[shell]\ndeny=["npm test"]\n')
    _write(catalog / "skills/unused/SKILL.md", "unused skill")
    _write(catalog / "mcp/unused.toml", "not even valid TOML")
    assert render_global(root) == before
    assert load_catalog(catalog / "nextjs.toml").skills()[0].name == "review-typescript"


def test_adding_second_template_refuses_different_shared_bytes(
    bare_project: Path, catalog: Path
) -> None:
    assert _template(bare_project, "vendor", "nextjs") == 0
    before = project_config_path(bare_project).read_bytes()
    (catalog / "instructions/typescript.md").write_text("new upstream")
    assert _template(bare_project, "vendor", "react-native") == 3
    assert project_config_path(bare_project).read_bytes() == before


def test_shared_skill_sync_removes_retired_support_file(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support = _write(catalog / "skills/review-typescript/scripts/check", "old script")
    support.chmod(0o755)
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    local = bare_project / "loadout/templates/skills/review-typescript/scripts/check"
    assert local.stat().st_mode & 0o777 == 0o755
    support.unlink()
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    assert _template(bare_project, "sync", "nextjs") == 0
    assert not local.exists()
    assert template_divergence(bare_project) == []
    assert tree_hash(vendored_path(bare_project, "nextjs")) == tree_hash(catalog / "nextjs.toml")


def test_removing_reference_keeps_part_needed_by_other_template(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    (catalog / "nextjs.toml").write_text('instructions = ["nextjs"]\n')
    assert _template(bare_project, "sync", "nextjs") == 0
    assert (
        bare_project / "loadout/templates/instructions/typescript.md"
    ).read_text() == "Shared TypeScript advice\n"
    assert template_divergence(bare_project) == []


def test_new_local_skill_file_during_confirmation_refuses_update(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    (catalog / "instructions/typescript.md").write_text("new upstream")
    before = project_config_path(bare_project).read_bytes()

    def answer(_: str) -> str:
        _write(bare_project / "loadout/templates/skills/review-typescript/new.txt", "user edit")
        return "yes"

    monkeypatch.setattr("builtins.input", answer)
    assert _template(bare_project, "sync", "nextjs") == 3
    assert project_config_path(bare_project).read_bytes() == before


def test_native_project_values_override_template_with_both_tiers_present(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path)
    config = load_project_config(project_config_path(tmp_path))
    assert config.artifacts is not None
    for artifact in config.artifacts.records:
        for part in artifact.parts:
            source = config.artifacts.source_root / part.source
            if part.category == "mcp":
                source.write_text(
                    json.dumps(
                        {"mcpServers": {"github": {"command": "project-mcp", "disabled": True}}}
                    )
                )
            elif part.category == "permissions":
                source.write_text(json.dumps({"permissions": {"deny": ["Bash(npm test:*)"]}}))
    _write(
        catalog / "mcp/github.toml",
        '[github]\ntransport = "stdio"\ncommand = "github-mcp"\n[extra]\ntransport = "stdio"\ncommand = "extra-mcp"\n',
    )
    _write(catalog / "permissions/node.toml", '[shell]\nallow = ["npm test", "npm run lint"]\n')
    assert _template(tmp_path, "vendor", "nextjs") == 0
    write_all(tmp_path)
    servers = json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert servers == {
        "github": {"command": "project-mcp", "disabled": True},
        "extra": {"type": "stdio", "command": "extra-mcp", "args": [], "env": {}},
    }
    permissions = json.loads((tmp_path / ".claude/settings.json").read_text())["permissions"]
    assert permissions["deny"] == ["Bash(npm test:*)"]
    assert permissions["allow"] == ["Bash(npm run lint:*)"]


def test_native_retirement_preserves_edits_and_then_removes_clean_output(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path)
    assert _template(tmp_path, "vendor", "nextjs") == 0
    write_all(tmp_path)
    generated = tmp_path / ".claude/skills/review-typescript/SKILL.md"
    original = generated.read_bytes()
    generated.write_text("user changes")
    _write(catalog / "nextjs.toml", 'instructions = ["nextjs"]\n')
    assert _template(tmp_path, "sync", "nextjs") == 0
    with pytest.raises(DeploymentConflict):
        write_all(tmp_path)
    assert generated.read_text() == "user changes"
    generated.write_bytes(original)
    write_all(tmp_path)
    assert not generated.exists()
    assert "Next.js advice" in (tmp_path / "CLAUDE.md").read_text()


def test_catalog_rejects_symlink_to_active_skill(catalog: Path) -> None:
    active = _write(catalog.parent / "skills/active/SKILL.md", "active")
    (catalog / "skills/link").symlink_to(active.parent)
    _write(catalog / "unsafe.toml", 'skills = ["link"]\n')
    with pytest.raises(LoadoutError, match="symlink"):
        load_catalog(catalog / "unsafe.toml")


def test_manifest_and_directory_same_name_are_ambiguous(tmp_path: Path, catalog: Path) -> None:
    (catalog / "nextjs").mkdir()
    with pytest.raises(LoadoutError, match="ambiguous"):
        resolve_template("nextjs", tmp_path)


def test_catalog_starter_import_freezes_all_referenced_parts(tmp_path: Path, catalog: Path) -> None:
    _write(catalog / "frontend.toml", (catalog / "nextjs.toml").read_text())
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude", "codex", "opencode", "pi")),
        starter="frontend",
    )
    assert plan.issues == ()
    assert plan.validated
    copied = {w.path.relative_to(plan.inventory.source_root) for w in plan.source_writes}
    assert Path("templates/skills/review-typescript/SKILL.md") in copied
    assert Path("templates/frontend.toml") in copied
    assert catalog / "instructions/typescript.md" in plan.starter_dependencies
    apply_migration(prepare_migration(plan))
    assert (tmp_path / ".agents/skills/review-typescript/SKILL.md").is_file()
    machine_config_path().unlink()
    assert check_staged(tmp_path) == 0


def test_staged_catalog_renders_without_machine_source(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path)
    assert _template(tmp_path, "vendor", "nextjs") == 0
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "loadout"], check=True)
    machine_config_path().unlink()
    assert check_staged(tmp_path) == 0


def test_native_unrepresentable_permission_refuses_without_mutation(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path, ("opencode",))
    config = load_project_config(project_config_path(tmp_path))
    assert config.artifacts is not None
    part = next(p for a in config.artifacts.records for p in a.parts if p.category == "permissions")
    (config.artifacts.source_root / part.source).write_text('{"permission": "allow"}\n')
    before = project_config_path(tmp_path).read_bytes()
    assert _template(tmp_path, "add", "nextjs") == 3
    assert project_config_path(tmp_path).read_bytes() == before


def test_native_routes_can_opt_out_of_catalog_parts(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path)
    _write(
        tmp_path / "loadout/extra-mcp.json", '{"mcpServers":{"private":{"command":"private-mcp"}}}'
    )
    index = tmp_path / "loadout/artifacts.toml"
    index.write_text(
        index.read_text()
        + '\n[[artifact]]\nagents = ["claude"]\noutput = "extra-mcp.json"\nformat = "json"\ntemplate_parts = false\n[artifact.parts]\nmcp = {source = "extra-mcp.json", keys = ["mcpServers"]}\n'
    )
    assert _template(tmp_path, "vendor", "nextjs") == 0
    write_all(tmp_path)
    assert json.loads((tmp_path / "extra-mcp.json").read_text()) == {
        "mcpServers": {"private": {"command": "private-mcp"}}
    }


def test_undeclared_catalog_sync_cannot_bypass_provenance(
    bare_project: Path, catalog: Path
) -> None:
    local = bare_project / "loadout/templates/nextjs.toml"
    _write(local, 'instructions = ["mine"]\n')
    own = _write(local.parent / "instructions/mine.md", "keep my local advice")
    before = project_config_path(bare_project).read_bytes()
    assert _template(bare_project, "sync", "nextjs") == 3
    assert own.read_text() == "keep my local advice"
    assert project_config_path(bare_project).read_bytes() == before


def test_shared_update_rolls_back_a_write_failure(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = _write(catalog / "skills/review-typescript/retired.sh", "retired script")
    retired.chmod(0o750)
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendor", name) == 0
    local = bare_project / "loadout/templates"

    def snapshot() -> dict[Path, tuple[bytes, int]]:
        return {
            p.relative_to(local): (p.read_bytes(), p.stat().st_mode & 0o7777)
            for p in local.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    original_config = project_config_path(bare_project).read_bytes()
    (catalog / "instructions/typescript.md").write_text("changed")
    retired.unlink()
    added = _write(catalog / "skills/review-typescript/added.sh", "new script")
    added.chmod(0o755)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    install = template_vendor.atomic_install

    def fail(path: Path, content: template_vendor.FrozenFile) -> None:
        if path == project_config_path(bare_project) and content.content != original_config:
            raise OSError("injected write failure")
        install(path, content)

    monkeypatch.setattr(template_vendor, "atomic_install", fail)
    assert _template(bare_project, "sync", "nextjs") == 3
    assert snapshot() == before
    assert project_config_path(bare_project).read_bytes() == original_config


def test_native_project_skill_replaces_whole_template_tree(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path)
    config = load_project_config(project_config_path(tmp_path))
    assert config.artifacts is not None
    part = next(p for a in config.artifacts.records for p in a.parts if p.category == "skills")
    own = config.artifacts.source_root / part.source / "review-typescript"
    _write(own / "SKILL.md", "Project skill")
    _write(own / "project.txt", "Project helper")
    _write(catalog / "skills/review-typescript/template.txt", "Template helper")
    _write(catalog / "skills/extra/SKILL.md", "Extra template skill")
    _write(catalog / "nextjs.toml", 'skills = ["review-typescript", "extra"]\n')
    assert _template(tmp_path, "vendor", "nextjs") == 0
    output = render_project(tmp_path)
    skill_dir = tmp_path / ".claude/skills"
    assert {p.relative_to(skill_dir) for p in output if p.is_relative_to(skill_dir)} == {
        Path("review-typescript/SKILL.md"),
        Path("review-typescript/project.txt"),
        Path("extra/SKILL.md"),
    }
    document = output[skill_dir / "review-typescript/SKILL.md"]
    helper = output[skill_dir / "review-typescript/project.txt"]
    assert isinstance(document, Copied)
    assert document.read_bytes() == b"Project skill"
    assert isinstance(helper, Copied)
    assert helper.read_bytes() == b"Project helper"
    assert "Extra template skill" in output[skill_dir / "extra/SKILL.md"]


def _part_source(root: Path, category: str) -> Path:
    config = load_project_config(project_config_path(root))
    assert config.artifacts is not None
    part = next(p for a in config.artifacts.records for p in a.parts if p.category == category)
    return config.artifacts.source_root / part.source


@pytest.mark.parametrize("definition", [False, True])
def test_codex_catalog_mcp_policy_survives_sync(
    tmp_path: Path, catalog: Path, definition: bool
) -> None:
    _native(tmp_path, ("codex",))
    _write(
        catalog / "policy.toml",
        'permissions=["policy"]\n' + ('mcp=["github"]\n' if definition else ""),
    )
    _write(
        catalog / "permissions/policy.toml",
        '[mcp]\ndeny=["blocked/*", "github/write"]\nask=["github/read"]\nallow=["github/list"]\n',
    )
    assert _template(tmp_path, "vendor", "policy") == 0
    write_all(tmp_path)
    expected = {
        "blocked": {"enabled": False},
        "github": {
            **({"command": "github-mcp"} if definition else {}),
            "disabled_tools": ["write"],
            "tools": {"list": {"approval_mode": "approve"}, "read": {"approval_mode": "prompt"}},
        },
    }
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"] == expected
    assert check_all(tmp_path) == []
    write_all(tmp_path)
    assert check_all(tmp_path) == []


@pytest.mark.parametrize("name", ["company.search", "company/search"])
def test_codex_catalog_server_names_are_literal(tmp_path: Path, catalog: Path, name: str) -> None:
    _native(tmp_path, ("codex",))
    _write(catalog / "names.toml", 'mcp=["names"]\n')
    _write(
        catalog / "mcp/names.toml",
        f'[{json.dumps(name)}]\ntransport="stdio"\ncommand="search-mcp"\n',
    )
    assert _template(tmp_path, "vendor", "names") == 0
    write_all(tmp_path)
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"] == {
        name: {"command": "search-mcp"}
    }


def test_order_sensitive_native_permissions_refuse_before_template_selection(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path, ("opencode",))
    _part_source(tmp_path, "permissions").write_text(
        '{"permission":{"*":"allow","bash":{"*":"deny"}}}'
    )
    before = project_config_path(tmp_path).read_bytes()
    assert _template(tmp_path, "add", "nextjs") == 3
    assert project_config_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("default", ["ask", "deny"])
def test_native_default_keeps_its_restriction(tmp_path: Path, catalog: Path, default: str) -> None:
    _native(tmp_path, ("opencode",))
    _part_source(tmp_path, "permissions").write_text(
        json.dumps({"permission": {"bash": {"*": default}}})
    )
    _write(catalog / "defaults.toml", 'permissions=["defaults"]\n')
    _write(catalog / "permissions/defaults.toml", '[shell]\ndefault="allow"\n')
    assert _template(tmp_path, "vendor", "defaults") == 0
    write_all(tmp_path)
    assert json.loads((tmp_path / "opencode.json").read_text())["permission"]["bash"] == {
        "*": default
    }


@pytest.mark.parametrize("portable", [False, True])
def test_optional_codex_permissions_receive_catalog_rules(
    tmp_path: Path, catalog: Path, portable: bool
) -> None:
    _native(tmp_path, ("codex",))
    index = tmp_path / "loadout/artifacts.toml"
    document = tomlkit.parse(index.read_text())
    route = next(r for r in document["artifact"] if r.get("category") == "permissions")
    route["optional"] = True
    if portable:
        del route["mode"]
        route["format"] = "text"
        route["renderer"] = "codex-project"
    index.write_text(tomlkit.dumps(document))
    _part_source(tmp_path, "permissions").unlink()
    assert _template(tmp_path, "vendor", "nextjs") == 0
    write_all(tmp_path)
    assert (
        'prefix_rule(pattern = ["npm", "test"], decision = "allow")'
        in (tmp_path / ".codex/rules/permissions.rules").read_text()
    )


def test_catalog_skills_honor_declared_modes(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path)
    _write(catalog / "skills/review-typescript/scripts/check", "run check")
    index = tmp_path / "loadout/artifacts.toml"
    document = tomlkit.parse(index.read_text())
    route = next(r for r in document["artifact"] if r.get("category") == "skills")
    route["modes"] = {"review-typescript/SKILL.md": 0o640, "review-typescript/scripts/check": 0o750}
    index.write_text(tomlkit.dumps(document))
    assert _template(tmp_path, "vendor", "nextjs") == 0
    assert main(["sync", "--root", str(tmp_path)]) == 0
    skill = tmp_path / ".claude/skills/review-typescript"
    assert (skill / "SKILL.md").stat().st_mode & 0o7777 == 0o640
    assert (skill / "scripts/check").stat().st_mode & 0o7777 == 0o750
    assert "Review TypeScript." in (skill / "SKILL.md").read_text()
    assert (skill / "scripts/check").read_text() == "run check"
    assert check_all(tmp_path) == []
    (skill / "SKILL.md").chmod(0o600)
    assert [path for path, _, _ in check_all(tmp_path)] == [skill / "SKILL.md"]
    with pytest.raises(DeploymentConflict):
        write_all(tmp_path)
    (skill / "SKILL.md").chmod(0o640)
    write_all(tmp_path)
    assert check_all(tmp_path) == []
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "loadout"], check=True)
    machine_config_path().unlink()
    assert check_staged(tmp_path) == 0


@pytest.mark.parametrize("native", [False, True])
def test_repeated_parts_keep_last_template_precedence(
    tmp_path: Path, catalog: Path, native: bool
) -> None:
    if native:
        _native(tmp_path, ("opencode",))
    else:
        _write(tmp_path / "loadout/config.toml", 'harnesses=["opencode"]\n')
        _write(tmp_path / "loadout/permissions.toml", "")
    for name, part in (("a", "shared"), ("b", "override"), ("c", "shared")):
        _write(
            catalog / f"{name}.toml",
            f'mcp=["{part}"]\npermissions=["{part}"]\ninstructions=["typescript"]\n',
        )
    for part, decision, extra in (("shared", "deny", "read"), ("override", "allow", "glob")):
        _write(
            catalog / f"mcp/{part}.toml",
            f'[github]\ntransport="stdio"\ncommand="{part}"\n[{extra}]\ntransport="stdio"\ncommand="{extra}"\n',
        )
        _write(
            catalog / f"permissions/{part}.toml",
            f'[opencode.extra]\nedit="{decision}"\n{extra}="ask"\n',
        )
    for name in ("a", "b", "c"):
        assert _template(tmp_path, "vendor", name) == 0
    write_all(tmp_path)
    result = json.loads((tmp_path / "opencode.json").read_text())
    assert result["mcp"] == {
        "github": {"type": "local", "command": ["shared"]},
        "read": {"type": "local", "command": ["read"]},
        "glob": {"type": "local", "command": ["glob"]},
    }
    assert {key: result["permission"][key] for key in ("edit", "read", "glob")} == {
        "edit": "deny",
        "read": "ask",
        "glob": "ask",
    }
    assert (tmp_path / "AGENTS.md").read_text().count("Shared TypeScript advice") == 1


@pytest.mark.parametrize("agent", ["claude", "codex", "opencode", "pi"])
def test_each_agent_receives_every_catalog_slice(tmp_path: Path, catalog: Path, agent: str) -> None:
    _native(tmp_path, (agent,))
    _write(
        catalog / "permissions/node.toml",
        '[shell]\nallow=["npm test"]\nask=["npm publish"]\ndeny=["rm"]\n'
        '[mcp]\nallow=["github/list"]\nask=["github/read"]\ndeny=["github/write"]\n',
    )
    _write(catalog / "skills/review-typescript/reference.md", "Review reference\n")
    assert _template(tmp_path, "vendor", "nextjs") == 0
    written = write_all(tmp_path)
    skill_root = tmp_path / (".agents" if agent == "codex" else f".{agent}") / "skills"
    assert "Review TypeScript." in (skill_root / "review-typescript/SKILL.md").read_text()
    assert (skill_root / "review-typescript/reference.md").read_text() == "Review reference\n"
    instructions = tmp_path / ("CLAUDE.md" if agent == "claude" else "AGENTS.md")
    assert instructions.read_text() == "Shared TypeScript advice\n\nNext.js advice\n\n"
    if agent in {"claude", "pi"}:
        assert json.loads((tmp_path / ".mcp.json").read_text()) == {
            "mcpServers": {
                "github": {"type": "stdio", "command": "github-mcp", "args": [], "env": {}}
            }
        }
    if agent == "claude":
        assert json.loads((tmp_path / ".claude/settings.json").read_text())["permissions"] == {
            "allow": ["Bash(npm test:*)", "mcp__github__list"],
            "ask": ["Bash(npm publish:*)", "mcp__github__read"],
            "deny": ["Bash(rm:*)", "mcp__github__write"],
        }
        assert json.loads((tmp_path / ".claude/mcp-permissions.json").read_text()) == {
            "allow": ["github/list"],
            "ask": ["github/read"],
            "deny": ["github/write"],
        }
    elif agent == "codex":
        assert (tmp_path / ".codex/rules/permissions.rules").read_text() == (
            "# Generated by loadout from loadout/permissions.toml. "
            "Edits to this file are replaced on the next sync.\n"
            'prefix_rule(pattern = ["npm", "test"], decision = "allow")\n'
            'prefix_rule(pattern = ["npm", "publish"], decision = "prompt")\n'
            'prefix_rule(pattern = ["rm"], decision = "forbidden")\n'
        )
        assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"] == {
            "github": {
                "command": "github-mcp",
                "disabled_tools": ["write"],
                "tools": {
                    "list": {"approval_mode": "approve"},
                    "read": {"approval_mode": "prompt"},
                },
            }
        }
    else:
        bash = {
            "npm test": "allow",
            "npm test *": "allow",
            "npm publish": "ask",
            "npm publish *": "ask",
            "rm": "deny",
            "rm *": "deny",
        }
        if agent == "opencode":
            document = json.loads((tmp_path / "opencode.json").read_text())
            assert document["mcp"] == {"github": {"type": "local", "command": ["github-mcp"]}}
            assert document["permission"] == {
                "bash": {"*": "ask", **bash},
                "github_list": "allow",
                "github_read": "ask",
                "github_write": "deny",
            }
        else:
            document = json.loads(
                (tmp_path / ".pi/extensions/pi-permission-system/config.json").read_text()
            )
            assert document == {
                "permission": {
                    "bash": bash,
                    "mcp": {
                        "github_list": "allow",
                        "github:list": "allow",
                        "github_read": "ask",
                        "github:read": "ask",
                        "github_write": "deny",
                        "github:write": "deny",
                    },
                }
            }
    before = {p: (p.read_bytes(), p.stat().st_mode & 0o7777) for p in written}
    assert check_all(tmp_path) == []
    assert write_all(tmp_path) == []
    assert {p: (p.read_bytes(), p.stat().st_mode & 0o7777) for p in written} == before
    assert check_all(tmp_path) == []


def test_codex_native_policy_and_settings_survive_template_removal(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path, ("codex",))
    native = {
        "github": {
            "command": "project-mcp",
            "startup_timeout_sec": 45,
            "default_tools_approval_mode": "prompt",
            "disabled_tools": ["write"],
            "tools": {
                "read": {"approval_mode": "prompt", "enabled": True},
                "status": {"enabled": False},
            },
        },
        "private": {"url": "https://example.test/mcp", "custom": "preserve"},
    }
    _part_source(tmp_path, "mcp").write_text(tomlkit.dumps({"mcp_servers": native}))
    _write(catalog / "policy.toml", 'mcp=["github"]\npermissions=["policy"]\n')
    _write(
        catalog / "permissions/policy.toml",
        '[mcp]\nallow=["github/*", "github/read", "github/write", "github/list"]\ndeny=["github/destroy"]\n',
    )
    assert _template(tmp_path, "vendor", "policy") == 0
    write_all(tmp_path)
    combined = {
        **native,
        "github": {
            **native["github"],
            "disabled_tools": ["destroy", "write"],
            "tools": {**native["github"]["tools"], "list": {"approval_mode": "approve"}},
        },
    }
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"] == combined
    assert check_all(tmp_path) == []
    _write(tmp_path / "loadout/templates/policy.toml", "")
    write_all(tmp_path)
    assert tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"] == native
    assert check_all(tmp_path) == []


@pytest.mark.parametrize(
    "policy",
    [
        {"enabled": "yes"},
        {"disabled_tools": "write"},
        {"tools": {"read": {"approval_mode": "unknown"}}},
    ],
)
def test_codex_unrepresentable_native_policy_refuses_before_selection(
    tmp_path: Path, catalog: Path, policy: dict[str, object]
) -> None:
    _native(tmp_path, ("codex",))
    _part_source(tmp_path, "mcp").write_text(tomlkit.dumps({"mcp_servers": {"github": policy}}))
    _write(catalog / "policy.toml", 'permissions=["policy"]\n')
    _write(catalog / "permissions/policy.toml", '[mcp]\nallow=["github/read"]\n')
    before = project_config_path(tmp_path).read_bytes()
    assert _template(tmp_path, "add", "policy") == 3
    assert project_config_path(tmp_path).read_bytes() == before


def test_codex_mcp_policy_requires_its_own_consumer(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path, ("codex",))
    index = tmp_path / "loadout/artifacts.toml"
    document = tomlkit.parse(index.read_text())
    route = next(r for r in document["artifact"] if "mcp" in r.get("parts", {}))
    route["template_parts"] = False
    index.write_text(tomlkit.dumps(document))
    _write(catalog / "policy.toml", 'permissions=["policy"]\n')
    _write(catalog / "permissions/policy.toml", '[mcp]\ndeny=["github/*"]\n')
    before = project_config_path(tmp_path).read_bytes()
    assert _template(tmp_path, "add", "policy") == 3
    assert project_config_path(tmp_path).read_bytes() == before


def test_required_codex_permission_source_stays_required(tmp_path: Path, catalog: Path) -> None:
    _native(tmp_path, ("codex",))
    _part_source(tmp_path, "permissions").unlink()
    before = project_config_path(tmp_path).read_bytes()
    assert _template(tmp_path, "vendor", "nextjs") == 3
    assert project_config_path(tmp_path).read_bytes() == before
    assert not (tmp_path / "loadout/templates/nextjs.toml").exists()


def test_portable_unstated_default_does_not_vote_against_template(
    tmp_path: Path, catalog: Path
) -> None:
    _native(tmp_path, ("opencode",))
    index = tmp_path / "loadout/artifacts.toml"
    document = tomlkit.parse(index.read_text())
    route = next(r for r in document["artifact"] if "permissions" in r.get("parts", {}))
    route["parts"]["permissions"]["renderer"] = "opencode"
    index.write_text(tomlkit.dumps(document))
    _part_source(tmp_path, "permissions").write_text('[shell]\ndeny=["rm"]\n')
    _write(catalog / "node.toml", 'permissions=["node"]\n')
    _write(catalog / "permissions/node.toml", '[shell]\ndefault="allow"\nallow=["npm test"]\n')
    assert _template(tmp_path, "vendor", "node") == 0
    write_all(tmp_path)
    assert json.loads((tmp_path / "opencode.json").read_text())["permission"]["bash"] == {
        "*": "allow",
        "npm test": "allow",
        "npm test *": "allow",
        "rm": "deny",
        "rm *": "deny",
    }


@pytest.mark.parametrize("native", [False, True])
def test_catalog_validation_is_shared_per_render_only(
    tmp_path: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch, native: bool
) -> None:
    if native:
        _native(tmp_path)
    else:
        _write(tmp_path / "loadout/config.toml", 'harnesses=["claude"]\n')
        _write(tmp_path / "loadout/permissions.toml", "")
    for name in ("nextjs", "react-native"):
        assert _template(tmp_path, "vendor", name) == 0
    calls: Counter[Path] = Counter()
    original = template_catalog._validate_part

    def counted(path: Path, category: str) -> tuple[Path, ...]:
        calls[path] += 1
        return original(path, category)

    monkeypatch.setattr(template_catalog, "_validate_part", counted)
    render_project(tmp_path)
    local = tmp_path / "loadout/templates"
    expected = {
        local / relative: 1
        for relative in (
            "skills/review-typescript",
            "instructions/typescript.md",
            "instructions/nextjs.md",
            "permissions/node.toml",
            "mcp/github.toml",
        )
    }
    assert calls == expected
    _write(local / "instructions/nextjs.md", "Changed advice\n")
    outputs = render_project(tmp_path)
    assert calls == dict.fromkeys(expected, 2)
    instructions = outputs[tmp_path / "CLAUDE.md"]
    assert "Changed advice" in (
        instructions.read_bytes().decode() if isinstance(instructions, Copied) else instructions
    )


def test_native_catalog_reads_each_document_source_once(
    tmp_path: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _native(tmp_path, ("claude", "codex", "opencode", "pi"))
    assert _template(tmp_path, "vendor", "nextjs") == 0
    config = load_project_config(project_config_path(tmp_path))
    assert config.artifacts is not None
    expected = Counter(
        config.artifacts.source_root / part.source
        for artifact in config.artifacts.records
        if artifact.format in {"json", "toml"}
        for part in artifact.parts
    )
    calls: Counter[Path] = Counter()
    original = artifacts._literal

    def counted(path: Path, format: str) -> dict[str, object]:
        calls[path] += 1
        return original(path, format)

    monkeypatch.setattr(artifacts, "_literal", counted)
    render_project(tmp_path)
    assert calls == expected
