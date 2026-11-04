from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from . import overlay_seams
from .composition import fragment_paths, load_fragment, render
from .emit import global_rules, global_servers, settings_document, skill_trees
from .harnesses import SHARED_SKILL_READERS, SliceOutput
from .manifest import Manifest, PermissionTarget
from .output import Copied
from .overlay_documents import OwnedJson
from .overlay_manifest import OverlayManifest
from .overlay_renderers import (
    claude_owned,
    codex_overlay_notes,
    droid_owned,
    opencode_owned,
    render_claude_local,
    render_codex_overlay_config,
)
from .permissions.renderers import (
    refuse_mcp_namespace_overlap,
    render_codex_overlay,
    render_pi_project,
)
from .permissions.rules import EMPTY_RULES, Rules
from .servers import (
    Server,
    render_claude_servers,
    render_droid_servers,
    render_pi_servers,
)
from .skill_policy import render_skill_for, selected_skill_agents
from .skills import SKILL_DOCUMENT, Skill, check_skill_agents, render_skill

OverlayOutput = str | Copied | OwnedJson

OVERLAY_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "instructions": SliceOutput(output="CLAUDE.local.md"),
        "permissions": SliceOutput(output=".claude/settings.local.json"),
        "skills": SliceOutput(output=".claude/skills"),
    },
    "codex": {
        "instructions": SliceOutput(output=".codex/config.toml"),
        "mcp": SliceOutput(output=".codex/config.toml"),
        "permissions": SliceOutput(output=".codex/rules"),
        "skills": SliceOutput(output=".agents/skills"),
    },
    "droid": {
        "instructions": SliceOutput(output=".factory/AGENTS.md"),
        "mcp": SliceOutput(output=".factory/mcp.json"),
        "permissions": SliceOutput(output=".factory/settings.local.json"),
        "skills": SliceOutput(output=".factory/skills"),
    },
    "opencode": {
        "instructions": SliceOutput(output="opencode.json"),
        "mcp": SliceOutput(output="opencode.json"),
        "permissions": SliceOutput(output="opencode.json"),
        "skills": SliceOutput(output=".opencode/skills"),
    },
    "pi": {
        "instructions": SliceOutput(output=".pi/APPEND_SYSTEM.md"),
        "mcp": SliceOutput(output=".pi/mcp.json"),
        "permissions": SliceOutput(output=".pi/extensions/pi-permission-system/config.json"),
        "skills": SliceOutput(output=".pi/skills"),
    },
}

PI_EMPTY_PERMISSIONS: dict[str, Any] = {"permission": {"bash": {}}}

OPENCODE_INSTRUCTIONS = PurePosixPath(".loadout/opencode-instructions.md")


@dataclass(frozen=True)
class TargetFacts:
    agents_md: bool
    claude_md_on_path: bool


@dataclass(frozen=True)
class OverlayRender:
    files: dict[Path, OverlayOutput]
    claude_servers: dict[str, Any]
    shared: dict[Path, str]
    notes: tuple[str, ...]


def _user_memory_file(environ: Mapping[str, str]) -> Path:
    config_dir = environ.get("CLAUDE_CONFIG_DIR") or ""
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    return Path(
        os.path.abspath((Path(config_dir) if config_dir else home / ".claude") / "CLAUDE.md")
    )


def gather_facts(target_root: Path, environ: Mapping[str, str] | None = None) -> TargetFacts:
    env = os.environ if environ is None else environ
    root = target_root.resolve()
    user_memory = _user_memory_file(env).resolve()
    candidates = (
        folder / name
        for folder in (root, *root.parents)
        for name in ("CLAUDE.md", ".claude/CLAUDE.md")
    )
    local_above = (folder / "CLAUDE.local.md" for folder in root.parents)
    found = any(path.is_file() and path.resolve() != user_memory for path in candidates)
    return TargetFacts(
        agents_md=(root / "AGENTS.md").is_file(),
        claude_md_on_path=found or any(path.is_file() for path in local_above),
    )


def _permission_target(
    manifest: Manifest, harness: str, slice_name: str
) -> PermissionTarget | None:
    name = harness if slice_name == "permissions" else f"{harness}-{slice_name}"
    return next((t for t in manifest.permissions if t.harness == harness and t.name == name), None)


def _target_rules(target: PermissionTarget, rules: Rules) -> Rules:
    return rules if target.select_all else EMPTY_RULES


def _instructions(overlay: OverlayManifest, harness: str, facts: TargetFacts) -> str | None:
    manifest = overlay.manifest
    target = next((t for t in manifest.targets if t.name == harness), None)
    if target is None or not target.fragments:
        return None
    if harness != "claude":
        return render(target, manifest)
    body = _joined_fragments(overlay, harness)
    assert body is not None
    return render_claude_local(body, import_agents=facts.agents_md and not facts.claude_md_on_path)


def _joined_fragments(overlay: OverlayManifest, harness: str) -> str | None:
    manifest = overlay.manifest
    target = next((t for t in manifest.targets if t.name == harness), None)
    if target is None or not target.fragments:
        return None
    return "\n\n".join(load_fragment(path) for path in fragment_paths(target, manifest))


def _settings_fragment(target: PermissionTarget, overlay: OverlayManifest) -> dict[str, Any]:
    return settings_document(target, overlay.manifest, overlay.folder)


def _with_content(owned: OwnedJson) -> OwnedJson | None:
    return owned if owned.keys or owned.rules else None


def _skill_files(overlay: OverlayManifest, root: Path) -> list[tuple[Path, str, OverlayOutput]]:
    manifest = overlay.manifest
    if not manifest.skills:
        return []
    trees = skill_trees(manifest)
    check_skill_agents(
        tuple(s for s in trees if render_skill_for(s.name, manifest.skill_policies)),
        frozenset(),
    )
    available = tuple(item.harness for item in manifest.skills)
    placed: list[tuple[Path, str, OverlayOutput]] = []
    for skill in trees:
        selected = selected_skill_agents(skill.name, available, manifest.skill_policies)
        rendered = render_skill_for(skill.name, manifest.skill_policies)
        for agent in _skill_agents(skill, selected, rendered):
            base = root / str(OVERLAY_PRESET[agent]["skills"].output)
            placed.extend(_skill_outputs(skill, agent, base, rendered))
    return placed


def _skill_agents(skill: Skill, selected: tuple[str, ...], rendered: bool) -> list[str]:
    remaining = list(selected)
    if "codex" not in selected:
        return remaining
    shared = frozenset(SHARED_SKILL_READERS).intersection(selected)
    variants = {
        agent: render_skill(skill, agent) if rendered else skill.document.read_bytes()
        for agent in shared
    }
    if len(variants) > 1 and len(set(variants.values())) == 1:
        remaining = [agent for agent in remaining if agent not in SHARED_SKILL_READERS]
        remaining.insert(0, "codex")
    return remaining


def _skill_outputs(
    skill: Skill, agent: str, base: Path, rendered: bool
) -> list[tuple[Path, str, OverlayOutput]]:
    directory = base / skill.name
    owner = f"{agent}.skills"
    document: OverlayOutput = render_skill(skill, agent) if rendered else Copied(skill.document)
    return [
        (directory / SKILL_DOCUMENT, owner, document),
        *(
            (directory / relative, owner, Copied(source=skill.supporting_root / relative))
            for relative in skill.supporting
        ),
    ]


def _json_outputs(
    overlay: OverlayManifest, root: Path, rules: Rules, servers: dict[str, Server]
) -> list[tuple[Path, str, OverlayOutput]]:
    manifest = overlay.manifest
    placed: list[tuple[Path, str, OverlayOutput]] = []

    def put(harness: str, slice_name: str, output: OverlayOutput | None) -> None:
        if output is not None:
            relative = str(OVERLAY_PRESET[harness][slice_name].output)
            placed.append((root / relative, f"{harness}.{slice_name}", output))

    if (target := _permission_target(manifest, "claude", "permissions")) is not None:
        owned = claude_owned(_target_rules(target, rules), _settings_fragment(target, overlay))
        put("claude", "permissions", _with_content(owned))
    if (target := _permission_target(manifest, "droid", "permissions")) is not None:
        owned = droid_owned(_target_rules(target, rules), _settings_fragment(target, overlay))
        put("droid", "permissions", _with_content(owned))
    if _permission_target(manifest, "droid", "mcp") is not None and servers:
        put("droid", "mcp", overlay_seams.serialize_json(render_droid_servers(servers)))
    if (target := _permission_target(manifest, "pi", "permissions")) is not None:
        document = render_pi_project(_target_rules(target, rules), {})
        if document != PI_EMPTY_PERMISSIONS:
            refuse_mcp_namespace_overlap("pi", rules, servers)
            put("pi", "permissions", overlay_seams.serialize_json(document))
    if _permission_target(manifest, "pi", "mcp") is not None and servers:
        refuse_mcp_namespace_overlap("pi", rules, servers)
        put("pi", "mcp", overlay_seams.serialize_json(render_pi_servers(servers)))
    return placed


def _codex_outputs(
    overlay: OverlayManifest, name: str, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, OverlayOutput]], tuple[str, ...]]:
    manifest = overlay.manifest
    permissions = _permission_target(manifest, "codex", "permissions")
    codex_rules = _target_rules(permissions, rules) if permissions is not None else EMPTY_RULES
    defined = servers if _permission_target(manifest, "codex", "mcp") is not None else {}
    instructions = _joined_fragments(overlay, "codex")
    placed: list[tuple[Path, str, OverlayOutput]] = []
    notes = codex_overlay_notes(codex_rules, defined)
    if instructions is not None or defined:
        text, _ = render_codex_overlay_config(instructions, codex_rules, defined)
        relative = str(OVERLAY_PRESET["codex"]["instructions"].output)
        placed.append((root / relative, "codex.config", text))
    if permissions is not None:
        document = render_codex_overlay(codex_rules)
        if "prefix_rule(" in document:
            label = f"{name}.rules"
            overlay_seams.codex_rules(document, label)
            directory = str(OVERLAY_PRESET["codex"]["permissions"].output)
            placed.append((root / directory / label, "codex.permissions", document))
    return placed, notes


def _opencode_outputs(
    overlay: OverlayManifest, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, OverlayOutput]], dict[Path, str]]:
    manifest = overlay.manifest
    target = next((t for t in manifest.targets if t.name == "opencode"), None)
    permissions = _permission_target(manifest, "opencode", "permissions")
    opencode_rules = _target_rules(permissions, rules) if permissions is not None else None
    defined = servers if _permission_target(manifest, "opencode", "mcp") is not None else {}
    shared: dict[Path, str] = {}
    instructions: Path | None = None
    if target is not None and target.fragments:
        instructions = overlay.folder / OPENCODE_INSTRUCTIONS
        shared[instructions] = render(target, manifest)
    refuse_mcp_namespace_overlap(
        "opencode", EMPTY_RULES if opencode_rules is None else opencode_rules, defined
    )
    owned = opencode_owned(opencode_rules, defined, instructions)
    relative = str(OVERLAY_PRESET["opencode"]["permissions"].output)
    placed: list[tuple[Path, str, OverlayOutput]] = (
        [(root / relative, "opencode.config", owned)] if owned.keys else []
    )
    return placed, shared


def render_overlay(
    overlay: OverlayManifest, name: str, target_root: Path, facts: TargetFacts
) -> OverlayRender:
    manifest = overlay.manifest
    rules = global_rules(manifest)
    servers = global_servers(manifest)

    placed: list[tuple[Path, str, OverlayOutput]] = []
    for harness in ("claude", "droid", "pi"):
        if (text := _instructions(overlay, harness, facts)) is not None:
            relative = str(OVERLAY_PRESET[harness]["instructions"].output)
            placed.append((target_root / relative, f"{harness}.instructions", text))
    placed.extend(_json_outputs(overlay, target_root, rules, servers))
    codex_placed, notes = _codex_outputs(overlay, name, target_root, rules, servers)
    opencode_placed, shared = _opencode_outputs(overlay, target_root, rules, servers)
    placed.extend(codex_placed)
    placed.extend(opencode_placed)
    placed.extend(_skill_files(overlay, target_root))

    files: dict[Path, OverlayOutput] = {}
    claimed: dict[Path, str] = {}
    for path, owner, output in placed:
        overlay_seams.claim(path, owner, claimed)
        files[path] = output

    claude_servers = (
        render_claude_servers(servers)
        if servers and _permission_target(manifest, "claude", "mcp") is not None
        else {}
    )
    return OverlayRender(files=files, claude_servers=claude_servers, shared=shared, notes=notes)
