from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import stealth_seams
from .composition import for_destination
from .emit import (
    skill_agents,
)
from .manifest import PermissionTarget, declaring_file
from .notices import cursor_permission_notices, devin_permission_notices
from .output import Copied
from .permissions.renderers import (
    codex_stealth_notes,
    refuse_mcp_namespace_overlap,
    render_codex_stealth,
    render_codex_stealth_config,
    render_cursor_project,
    render_devin_project,
    render_pi_project,
)
from .permissions.rules import EMPTY_RULES, Rules
from .personal import AdoptedFacts, PersonalSource, adopted_claude_servers, permission_target
from .servers import (
    Server,
    render_claude_servers,
    render_cursor_servers,
    render_devin_servers,
    render_droid_servers,
    render_pi_servers,
)
from .skill_policy import aliased_skill, render_skill_for, selected_skill_agents, skill_aliases_in
from .skills import SKILL_DOCUMENT, Skill, check_skill_agents, render_skill, skill_source
from .stealth_documents import OwnedJson
from .stealth_manifest import StealthManifest
from .stealth_preset import (
    NO_FACTS,
    OPENCODE_INSTRUCTIONS,
    STEALTH_PRESET,
    TargetFacts,
    instruction_document,
)
from .stealth_renderers import claude_owned, droid_owned, opencode_owned

StealthOutput = str | Copied | OwnedJson

PI_EMPTY_PERMISSIONS: dict[str, Any] = {"permission": {"bash": {}}}

CURSOR_EMPTY_PERMISSIONS: dict[str, Any] = {"permissions": {"allow": [], "deny": []}}

DEVIN_EMPTY_PERMISSIONS: dict[str, Any] = {"permissions": {"allow": [], "ask": [], "deny": []}}


@dataclass(frozen=True)
class StealthRender:
    files: dict[Path, StealthOutput]
    claude_servers: dict[str, Any]
    shared: dict[Path, str]
    notes: tuple[str, ...]


def _target_rules(target: PermissionTarget, rules: Rules) -> Rules:
    return rules if target.select_all else EMPTY_RULES


def _instructions(stealth: StealthManifest, harness: str, facts: TargetFacts) -> str | None:
    fragments = stealth.instruction_fragments(harness)
    return None if fragments is None else instruction_document(harness, (fragments,), facts)


def _with_content(owned: OwnedJson) -> OwnedJson | None:
    return owned if owned.keys or owned.rules else None


def _skill_files(stealth: StealthManifest, root: Path) -> list[tuple[Path, str, StealthOutput]]:
    manifest = stealth.manifest
    if not manifest.skills:
        return []
    trees = stealth.skill_trees
    check_skill_agents(
        tuple(s for s in trees if render_skill_for(s.name, manifest.skill_policies)),
        frozenset(),
    )
    available = tuple(item.harness for item in manifest.skills)
    aliases = skill_aliases_in(declaring_file(manifest), trees, manifest.skill_policies)
    placed: list[tuple[Path, str, StealthOutput]] = []
    for original in trees:
        selected = selected_skill_agents(original.name, available, manifest.skill_policies)
        rendered = render_skill_for(original.name, manifest.skill_policies)
        skill = aliased_skill(original, aliases)
        for agent in skill_agents(skill, selected, rendered):
            base = root / str(STEALTH_PRESET[agent]["skills"].output)
            placed.extend(_skill_outputs(skill, agent, base, rendered))
    return placed


def _skill_outputs(
    skill: Skill, agent: str, base: Path, rendered: bool
) -> list[tuple[Path, str, StealthOutput]]:
    directory = base / skill.name
    owner = f"{agent}.skills"
    document: StealthOutput = render_skill(skill, agent) if rendered else skill_source(skill)
    return [
        (directory / SKILL_DOCUMENT, owner, document),
        *(
            (directory / relative, owner, Copied(source=skill.supporting_root / relative))
            for relative in skill.supporting
        ),
    ]


def _json_outputs(
    stealth: StealthManifest, root: Path, rules: Rules, servers: dict[str, Server]
) -> list[tuple[Path, str, StealthOutput]]:
    manifest = stealth.manifest
    placed: list[tuple[Path, str, StealthOutput]] = []

    def put(harness: str, slice_name: str, output: StealthOutput | None) -> None:
        if output is not None:
            relative = str(STEALTH_PRESET[harness][slice_name].output)
            placed.append((root / relative, f"{harness}.{slice_name}", output))

    if (target := permission_target(manifest, "claude", "permissions")) is not None:
        owned = claude_owned(_target_rules(target, rules), stealth.settings_fragment(target))
        put("claude", "permissions", _with_content(owned))
    if (target := permission_target(manifest, "droid", "permissions")) is not None:
        owned = droid_owned(_target_rules(target, rules), stealth.settings_fragment(target))
        put("droid", "permissions", _with_content(owned))
    if permission_target(manifest, "droid", "mcp") is not None and servers:
        put("droid", "mcp", stealth_seams.serialize_json(render_droid_servers(servers)))
    if (target := permission_target(manifest, "pi", "permissions")) is not None:
        document = render_pi_project(_target_rules(target, rules), {})
        if document != PI_EMPTY_PERMISSIONS:
            refuse_mcp_namespace_overlap("pi", rules, servers)
            put("pi", "permissions", stealth_seams.serialize_json(document))
    if permission_target(manifest, "pi", "mcp") is not None and servers:
        refuse_mcp_namespace_overlap("pi", rules, servers)
        put("pi", "mcp", stealth_seams.serialize_json(render_pi_servers(servers)))
    return placed


def _codex_outputs(
    stealth: StealthManifest, name: str, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, StealthOutput]], tuple[str, ...]]:
    manifest = stealth.manifest
    permissions = permission_target(manifest, "codex", "permissions")
    codex_rules = _target_rules(permissions, rules) if permissions is not None else EMPTY_RULES
    defined = servers if permission_target(manifest, "codex", "mcp") is not None else {}
    instructions = stealth.instruction_fragments("codex")
    placed: list[tuple[Path, str, StealthOutput]] = []
    notes = codex_stealth_notes(codex_rules, defined)
    if instructions is not None or defined:
        text, _ = render_codex_stealth_config(instructions, codex_rules, defined)
        relative = str(STEALTH_PRESET["codex"]["instructions"].output)
        placed.append((root / relative, "codex.config", text))
    if permissions is not None:
        document = render_codex_stealth(codex_rules)
        if "prefix_rule(" in document:
            label = f"{name}.rules"
            stealth_seams.codex_rules(document, label)
            directory = str(STEALTH_PRESET["codex"]["permissions"].output)
            placed.append((root / directory / label, "codex.permissions", document))
    return placed, notes


def opencode_shared(stealth: StealthManifest, fragments: str | None) -> dict[Path, str]:
    if fragments is None:
        return {}
    document = instruction_document("opencode", (fragments,), NO_FACTS)
    return {stealth.folder / OPENCODE_INSTRUCTIONS: document}


def _opencode_outputs(
    stealth: StealthManifest, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, StealthOutput]], dict[Path, str]]:
    manifest = stealth.manifest
    fragments = stealth.instruction_fragments("opencode")
    permissions = permission_target(manifest, "opencode", "permissions")
    opencode_rules = _target_rules(permissions, rules) if permissions is not None else None
    defined = servers if permission_target(manifest, "opencode", "mcp") is not None else {}
    shared = opencode_shared(stealth, fragments)
    instructions = next(iter(shared), None)
    refuse_mcp_namespace_overlap(
        "opencode", EMPTY_RULES if opencode_rules is None else opencode_rules, defined
    )
    owned = opencode_owned(opencode_rules, defined, instructions)
    relative = str(STEALTH_PRESET["opencode"]["permissions"].output)
    placed: list[tuple[Path, str, StealthOutput]] = (
        [(root / relative, "opencode.config", owned)] if owned.keys else []
    )
    return placed, shared


def _cursor_outputs(
    stealth: StealthManifest, name: str, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, StealthOutput]], tuple[str, ...]]:
    manifest = stealth.manifest
    preset = STEALTH_PRESET["cursor"]
    placed: list[tuple[Path, str, StealthOutput]] = []
    if (fragments := stealth.instruction_fragments("cursor")) is not None:
        rule = root / str(preset["instructions"].output) / f"{name}.mdc"
        text = instruction_document("cursor", (fragments,), NO_FACTS)
        placed.append((rule, "cursor.instructions", for_destination(rule, text)))
    notes: tuple[str, ...] = ()
    if (permissions := permission_target(manifest, "cursor", "permissions")) is not None:
        cursor_rules = _target_rules(permissions, rules)
        notes = tuple(
            f"cursor: {notice.message}"
            for notice in cursor_permission_notices("cursor", "cursor-project", cursor_rules)
        )
        settings = render_cursor_project(cursor_rules, {})
        if settings != CURSOR_EMPTY_PERMISSIONS:
            relative = str(preset["permissions"].output)
            placed.append(
                (root / relative, "cursor.permissions", stealth_seams.serialize_json(settings))
            )
    if permission_target(manifest, "cursor", "mcp") is not None and servers:
        config = render_cursor_servers(servers)
        relative = str(preset["mcp"].output)
        placed.append((root / relative, "cursor.mcp", stealth_seams.serialize_json(config)))
    return placed, notes


def _devin_outputs(
    stealth: StealthManifest, name: str, root: Path, rules: Rules, servers: dict[str, Server]
) -> tuple[list[tuple[Path, str, StealthOutput]], tuple[str, ...]]:
    manifest = stealth.manifest
    preset = STEALTH_PRESET["devin"]
    placed: list[tuple[Path, str, StealthOutput]] = []
    if (fragments := stealth.instruction_fragments("devin")) is not None:
        rule = root / str(preset["instructions"].output) / f"{name}.md"
        text = instruction_document("devin", (fragments,), NO_FACTS)
        placed.append((rule, "devin.instructions", for_destination(rule, text)))
    notes: tuple[str, ...] = ()
    if (permissions := permission_target(manifest, "devin", "permissions")) is not None:
        devin_rules = _target_rules(permissions, rules)
        notes = tuple(
            f"devin: {notice.message}"
            for notice in devin_permission_notices("devin", "devin-project", devin_rules)
        )
        settings = render_devin_project(devin_rules, {})
        if settings != DEVIN_EMPTY_PERMISSIONS:
            relative = str(preset["permissions"].output)
            placed.append(
                (root / relative, "devin.permissions", stealth_seams.serialize_json(settings))
            )
    if permission_target(manifest, "devin", "mcp") is not None and servers:
        relative = str(preset["mcp"].output)
        config = stealth_seams.serialize_json(render_devin_servers(servers))
        placed.append((root / relative, "devin.mcp", config))
    return placed, notes


def render_stealth(
    stealth: StealthManifest, name: str, target_root: Path, facts: TargetFacts
) -> StealthRender:
    manifest = stealth.manifest
    rules = stealth.rules
    servers = stealth.servers

    placed: list[tuple[Path, str, StealthOutput]] = []
    for harness in ("claude", "droid", "pi"):
        if (text := _instructions(stealth, harness, facts)) is not None:
            relative = str(STEALTH_PRESET[harness]["instructions"].output)
            placed.append((target_root / relative, f"{harness}.instructions", text))
    placed.extend(_json_outputs(stealth, target_root, rules, servers))
    codex_placed, notes = _codex_outputs(stealth, name, target_root, rules, servers)
    opencode_placed, shared = _opencode_outputs(stealth, target_root, rules, servers)
    cursor_placed, cursor_notes = _cursor_outputs(stealth, name, target_root, rules, servers)
    devin_placed, devin_notes = _devin_outputs(stealth, name, target_root, rules, servers)
    placed.extend(codex_placed)
    placed.extend(cursor_placed)
    placed.extend(devin_placed)
    placed.extend(opencode_placed)
    placed.extend(_skill_files(stealth, target_root))

    files: dict[Path, StealthOutput] = {}
    claimed: dict[Path, str] = {}
    for path, owner, output in placed:
        stealth_seams.claim(path, owner, claimed)
        files[path] = output

    claude_servers = (
        render_claude_servers(servers)
        if servers and permission_target(manifest, "claude", "mcp") is not None
        else {}
    )
    return StealthRender(
        files=files,
        claude_servers=claude_servers,
        shared=shared,
        notes=(*notes, *cursor_notes, *devin_notes),
    )


def render_adopted(stealth: StealthManifest, name: str, facts: AdoptedFacts) -> StealthRender:
    source = PersonalSource(name, stealth, "matched")
    return StealthRender(
        files={},
        claude_servers=adopted_claude_servers(facts, source),
        shared=opencode_shared(stealth, stealth.instruction_fragments("opencode")),
        notes=(),
    )
