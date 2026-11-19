from __future__ import annotations

import json
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from itertools import takewhile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .composition import for_destination
from .errors import LoadoutError
from .json_paths import JsonPath, display_path, with_path
from .manifest import MANIFEST_NAME, Manifest, PermissionTarget
from .output import Merged, Output
from .permissions.merge import merge_rules
from .permissions.renderers import MCP_NAMESPACE_HARNESS, refuse_mcp_namespace_overlap
from .permissions.rules import EMPTY_RULES, Rules
from .project import load_project_config, project_config_path, project_slices
from .servers import Server, render_claude_servers
from .skill_policy import render_skill_for, selected_skill_agents, skill_aliases
from .skills import Skill, check_skill_agents, renamed_skill
from .stealth_git import GitFailure, identity
from .stealth_lock import same_path
from .stealth_registry import RegisteredStealth, load_registry, stealth_path
from .toml_paths import key_name
from .written import WrittenEntry, recorded_keys

if TYPE_CHECKING:
    from .stealth_manifest import StealthManifest
    from .stealth_render import TargetFacts
    from .stealth_targets import HarnessHomes, SkippedTarget, Target

BELOW_TOP_LEVEL = (
    "the project is below the repository's top level, so no stealth source renders a "
    "personal tier here"
)


@dataclass(frozen=True)
class PersonalSource:
    name: str
    stealth: StealthManifest | None
    health: Literal["matched", "unknown"]
    problem: str | None = None


@dataclass(frozen=True)
class PersonalTier:
    sources: tuple[PersonalSource, ...]
    note: str | None = None

    @property
    def matched(self) -> tuple[tuple[str, StealthManifest], ...]:
        return tuple(
            (source.name, source.stealth)
            for source in self.sources
            if source.health == "matched" and source.stealth is not None
        )


NO_PERSONAL = PersonalTier(())


@dataclass(frozen=True)
class PersonalReport:
    refusals: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    contributors: tuple[str, ...] = ()
    held: tuple[str, ...] = ()


RenderCheck = Callable[[str, Rules, Mapping[str, Server]], LoadoutError | None]


def no_check(harness: str, rules: Rules, servers: Mapping[str, Server]) -> LoadoutError | None:
    return None


@dataclass(frozen=True)
class PersonalInputs:
    rules: Mapping[str, Rules]
    servers: Mapping[str, Mapping[str, Server]]
    report: PersonalReport
    added: Mapping[str, Mapping[str, tuple[str, Server]]] = field(default_factory=dict)


def permission_target(manifest: Manifest, harness: str, slice_name: str) -> PermissionTarget | None:
    name = harness if slice_name == "permissions" else f"{harness}-{slice_name}"
    return next((t for t in manifest.permissions if t.harness == harness and t.name == name), None)


def clash_refusal(  # noqa: PLR0913
    source: str,
    kind: str,
    name: str,
    manifest: Path,
    other: str | None = None,
    *,
    original: str | None = None,
) -> str:
    winner = "the team's" if other is None else f"stealth {other}'s"
    table = "skills" if kind == "skill" else "servers"
    key = original or name
    local = (
        f" or name its folder {key}.local to replace the team's"
        if kind == "skill" and other is None
        else ""
    )
    return (
        f"refused: stealth {source}: its {kind} {name} clashes with {winner} {kind} {name}, "
        f'which wins; rename yours with [{table}.{key}] name = "<new>" in {manifest}{local}'
    )


def _original_server(stealth: StealthManifest, server: str) -> str | None:
    return next(
        (policy.name for policy in stealth.manifest.server_policies if policy.alias == server),
        None,
    )


def _probe(root: Path, homes: HarnessHomes) -> Target | SkippedTarget:
    from .stealth_targets import probe  # noqa: PLC0415 - stealth_targets imports emit

    return probe(root, homes, None)


def _reachable(root: Path) -> bool:
    from .stealth_targets import Target, harness_homes  # noqa: PLC0415 - stealth_targets imports emit

    resolved = str(root.resolve())
    if "\n" in resolved or "\r" in resolved:
        return False
    return isinstance(_probe(root, harness_homes()), Target)


def adopted_target(root: Path) -> bool:
    return project_config_path(root).is_file() and _reachable(root)


def stranded_target(root: Path) -> bool:
    return root.is_dir() and not os.path.lexists(project_config_path(root)) and _reachable(root)


def _below_top_level(root: Path) -> bool:
    try:
        found = identity(root.resolve())
    except GitFailure:
        return False
    return found is not None and not same_path(found.toplevel, root.resolve())


def _unknown(name: str, problem: str) -> PersonalSource:
    return PersonalSource(name, None, "unknown", problem)


def _source(registered: RegisteredStealth, root: Path, home: Path) -> PersonalSource | None:
    from .stealth_manifest import load_stealth_manifest  # noqa: PLC0415 - it imports emit
    from .stealth_targets import matches_root, stealth_folder_reason  # noqa: PLC0415 - same

    try:
        stealth = load_stealth_manifest(registered.folder)
    except (LoadoutError, OSError) as error:
        return _unknown(registered.name, str(error))
    if not matches_root(stealth.settings.targets, root, home):
        return None
    if registered.removing:
        return _unknown(
            registered.name,
            f"it is being removed; finish with `loadout stealth remove {registered.name}`",
        )
    if (reason := stealth_folder_reason(root, registered.folder)) is not None:
        return _unknown(registered.name, reason)
    try:
        _ = (stealth.kits, stealth.rules, stealth.servers, stealth.skill_trees)
    except (LoadoutError, OSError) as error:
        return _unknown(registered.name, str(error))
    return PersonalSource(registered.name, stealth, "matched")


def _targets_root(registered: RegisteredStealth, root: Path, home: Path) -> bool:
    from .stealth_manifest import load_stealth_manifest  # noqa: PLC0415 - it imports emit
    from .stealth_targets import matches_root  # noqa: PLC0415 - stealth_targets imports emit

    try:
        stealth = load_stealth_manifest(registered.folder)
    except (LoadoutError, OSError):
        return False
    return matches_root(stealth.settings.targets, root, home)


def personal_tier(root: Path, *, retiring: Collection[str] = ()) -> PersonalTier:
    from .stealth_targets import SkippedTarget, harness_homes  # noqa: PLC0415 - stealth_targets imports emit

    if not project_config_path(root).is_file():
        return NO_PERSONAL
    problem: str | None = None
    registered: tuple[RegisteredStealth, ...] = ()
    try:
        registered = tuple(
            entry for entry in load_registry(stealth_path()) if entry.name not in retiring
        )
    except LoadoutError as error:
        problem = str(error)
    if not registered and problem is None:
        return NO_PERSONAL
    homes = harness_homes()
    probed = _probe(root, homes)
    if isinstance(probed, SkippedTarget):
        if probed.kind == "git-failed" and any(
            _targets_root(entry, root, homes.home) for entry in registered
        ):
            return PersonalTier((_unknown("checkout", probed.reason),))
        return PersonalTier((), BELOW_TOP_LEVEL if _below_top_level(root) else None)
    if problem is not None:
        return PersonalTier((_unknown("registry", problem),))
    found = (_source(entry, root.resolve(), homes.home) for entry in registered)
    return PersonalTier(tuple(source for source in found if source is not None))


def _source_rules(stealth: StealthManifest, harness: str) -> Rules | None:
    target = permission_target(stealth.manifest, harness, "permissions")
    if target is None or not target.select_all or stealth.rules == Rules():
        return None
    return stealth.rules


def _offers_servers(stealth: StealthManifest, harness: str) -> bool:
    return harness != "claude" and permission_target(stealth.manifest, harness, "mcp") is not None


_STRICTNESS = {"allow": 0, "ask": 1, "deny": 2}


def _merge_personal(current: Rules, added: Rules) -> Rules:
    merged = merge_rules(current, added)
    extra = {
        key: max(value, added.opencode_extra[key], key=lambda v: _STRICTNESS.get(v, -1))
        if key in added.opencode_extra
        and value in _STRICTNESS
        and added.opencode_extra[key] in _STRICTNESS
        else value
        for key, value in current.opencode_extra.items()
    }
    return replace(merged, opencode_extra={**merged.opencode_extra, **extra})


def _namespaces(harness: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            MCP_NAMESPACE_HARNESS[spec.renderer]
            for _, _, spec in project_slices((harness,))
            if spec.renderer in MCP_NAMESPACE_HARNESS
        )
    )


def _overlap(
    harness: str, rules: Rules, servers: Mapping[str, Server], check: RenderCheck
) -> LoadoutError | None:
    try:
        for namespace in _namespaces(harness):
            refuse_mcp_namespace_overlap(namespace, rules, servers)
    except LoadoutError as error:
        return error
    return check(harness, rules, servers)


def _missing_harnesses(name: str, stealth: StealthManifest, harnesses: Sequence[str]) -> str | None:
    missing = [harness for harness in stealth.harnesses if harness not in harnesses]
    if not missing:
        return None
    return (
        f"stealth {name}: this project does not declare {', '.join(missing)}, so nothing "
        "personal renders for it"
    )


def personal_inputs(
    tier: PersonalTier,
    harnesses: Sequence[str],
    team_rules: Rules,
    team_servers: Mapping[str, Server],
    check: RenderCheck = no_check,
) -> PersonalInputs:
    rules: dict[str, Rules] = {}
    servers: dict[str, dict[str, Server]] = {}
    added: dict[str, dict[str, tuple[str, Server]]] = {}
    refusals: list[str] = []
    contributors: list[str] = []
    for harness in harnesses:
        current_rules, current = team_rules, dict(team_servers)
        owners: dict[str, str] = {}
        added[harness] = {}
        for name, stealth in tier.matched:
            if harness not in stealth.harnesses:
                continue
            manifest = stealth.folder / MANIFEST_NAME
            added_rules = _source_rules(stealth, harness)
            offered = stealth.servers if _offers_servers(stealth, harness) else {}
            accepted: dict[str, Server] = {}
            for server, definition in offered.items():
                original = _original_server(stealth, server)
                if server in team_servers:
                    refusals.append(
                        clash_refusal(name, "server", server, manifest, original=original)
                    )
                elif server in owners:
                    refusals.append(
                        clash_refusal(
                            name, "server", server, manifest, owners[server], original=original
                        )
                    )
                else:
                    accepted[server] = definition
            if added_rules is None and not accepted:
                continue
            merged = (
                _merge_personal(current_rules, added_rules)
                if added_rules is not None
                else current_rules
            )
            widened = {**current, **accepted}
            error = _overlap(harness, merged, widened, check)
            if error is not None and _overlap(harness, current_rules, current, check) is None:
                refusals.append(
                    f"refused: stealth {name}: {error}; none of its rules or servers "
                    f"render for {harness}"
                )
                continue
            current_rules, current = merged, widened
            owners.update(dict.fromkeys(accepted, name))
            added[harness].update({key: (name, accepted[key]) for key in accepted})
            contributors.append(name)
        rules[harness], servers[harness] = current_rules, current
    notes = [
        note
        for name, stealth in tier.matched
        if (note := _missing_harnesses(name, stealth, harnesses)) is not None
    ]
    unknown = [source for source in tier.sources if source.health == "unknown"]
    report = PersonalReport(
        refusals=tuple(
            dict.fromkeys(
                (
                    *refusals,
                    *(f"refused: stealth {s.name}: {s.problem}" for s in unknown),
                )
            )
        ),
        notes=(*notes, *((tier.note,) if tier.note is not None else ())),
        contributors=tuple(dict.fromkeys(contributors)),
        held=tuple(source.name for source in unknown),
    )
    return PersonalInputs(rules, servers, report, added)


def _claude_servers_offered(stealth: StealthManifest) -> bool:
    return (
        "claude" in stealth.harnesses
        and permission_target(stealth.manifest, "claude", "mcp") is not None
    )


def claude_server_selection(
    sources: Sequence[tuple[str, StealthManifest]],
    harnesses: Sequence[str],
    team_servers: Mapping[str, Server],
) -> tuple[dict[str, dict[str, Server]], tuple[str, ...]]:
    if "claude" not in harnesses:
        return {}, ()
    accepted: dict[str, dict[str, Server]] = {}
    owners: dict[str, str] = {}
    refusals: list[str] = []
    for name, stealth in sources:
        if not _claude_servers_offered(stealth):
            continue
        manifest = stealth.folder / MANIFEST_NAME
        mine: dict[str, Server] = {}
        for server, definition in stealth.servers.items():
            original = _original_server(stealth, server)
            if server in team_servers:
                refusals.append(clash_refusal(name, "server", server, manifest, original=original))
            elif server in owners:
                refusals.append(
                    clash_refusal(
                        name, "server", server, manifest, owners[server], original=original
                    )
                )
            else:
                mine[server] = definition
                owners[server] = name
        accepted[name] = mine
    return accepted, tuple(refusals)


def adopted_claude_servers(
    root: Path, source: PersonalSource, team_servers: Mapping[str, Server]
) -> dict[str, Any]:
    if source.stealth is None:
        return {}
    harnesses = load_project_config(project_config_path(root)).harnesses
    earlier = tuple(takewhile(lambda entry: entry[0] != source.name, personal_tier(root).matched))
    accepted, _ = claude_server_selection(
        (*earlier, (source.name, source.stealth)), harnesses, team_servers
    )
    return render_claude_servers(accepted.get(source.name, {}))


@dataclass(frozen=True)
class PersonalSkill:
    source: str
    skill: Skill
    agents: tuple[str, ...]
    rendered: bool


@dataclass(frozen=True)
class PersonalSkills:
    skills: tuple[PersonalSkill, ...] = ()
    refusals: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def _source_skills(
    name: str, stealth: StealthManifest, available: Sequence[str]
) -> tuple[PersonalSkill, ...]:
    manifest = stealth.manifest
    if not manifest.skills:
        return ()
    policies = manifest.skill_policies
    trees = stealth.skill_trees
    check_skill_agents(tuple(s for s in trees if render_skill_for(s.name, policies)), frozenset())
    try:
        aliases = skill_aliases((skill.name for skill in trees), policies)
    except LoadoutError as error:
        raise LoadoutError(f"{stealth.folder / MANIFEST_NAME}: {error}") from error
    offered = tuple(item.harness for item in manifest.skills if item.harness in available)
    return tuple(
        PersonalSkill(
            name,
            renamed_skill(skill, aliases[skill.name]) if skill.name in aliases else skill,
            agents,
            render_skill_for(skill.name, policies),
        )
        for skill in trees
        if (agents := selected_skill_agents(skill.name, offered, policies))
    )


def personal_skills(
    tier: PersonalTier, available: Sequence[str], team: Mapping[str, Sequence[str]]
) -> PersonalSkills:
    owners: dict[str, str] = {}
    kept: list[PersonalSkill] = []
    refusals: list[str] = []
    for name, stealth in tier.matched:
        manifest = stealth.folder / MANIFEST_NAME
        try:
            offered = _source_skills(name, stealth, available)
        except (LoadoutError, OSError) as error:
            refusals.append(f"refused: stealth {name}: {error}; none of its skills render")
            continue
        for placed in offered:
            skill = placed.skill
            if skill.name in owners:
                refusals.append(
                    clash_refusal(
                        name,
                        "skill",
                        skill.name,
                        manifest,
                        owners[skill.name],
                        original=skill.original,
                    )
                )
            elif team.get(skill.name) and not skill.local:
                refusals.append(
                    clash_refusal(name, "skill", skill.name, manifest, original=skill.original)
                )
            else:
                owners[skill.name] = name
                kept.append(placed)
    return PersonalSkills(tuple(kept), tuple(refusals))


@dataclass(frozen=True)
class InstructionPart:
    source: str
    folder: Path
    text: str


@dataclass(frozen=True)
class PersonalSetting:
    source: str
    path: JsonPath
    value: Any


@dataclass(frozen=True)
class PersonalParts:
    instructions: Mapping[str, tuple[InstructionPart, ...]] = field(default_factory=dict)
    settings: Mapping[str, tuple[PersonalSetting, ...]] = field(default_factory=dict)
    refusals: tuple[str, ...] = ()

    @property
    def contributors(self) -> frozenset[str]:
        return frozenset(
            {
                *(part.source for parts in self.instructions.values() for part in parts),
                *(setting.source for found in self.settings.values() for setting in found),
            }
        )


SETTINGS_HARNESSES = ("claude", "droid")


def setting_refusal(source: str, harness: str, path: JsonPath, manifest: Path, other: str) -> str:
    key = display_path(path)
    return (
        f"refused: stealth {source}: its {harness} setting {key} clashes with stealth "
        f"{other}'s {harness} setting {key}, which wins; remove yours from the {harness} "
        f"settings fragment in {manifest}"
    )


def personal_parts(tier: PersonalTier, harnesses: Sequence[str]) -> PersonalParts:
    if not tier.matched:
        return PersonalParts()
    from .stealth_render import (  # noqa: PLC0415 - stealth_render imports emit
        instruction_fragments,
        settings_fragment,
    )
    from .stealth_renderers import setting_keys  # noqa: PLC0415 - it imports emit

    instructions: dict[str, list[InstructionPart]] = {}
    settings: dict[str, list[PersonalSetting]] = {}
    owners: dict[tuple[str, JsonPath], str] = {}
    refusals: list[str] = []
    for name, stealth in tier.matched:
        manifest = stealth.folder / MANIFEST_NAME
        shared = tuple(harness for harness in harnesses if harness in stealth.harnesses)
        for harness in shared:
            try:
                text = instruction_fragments(stealth, harness)
            except (LoadoutError, OSError) as error:
                refusals.append(
                    f"refused: stealth {name}: {error}; none of its instructions render "
                    f"for {harness}"
                )
                continue
            if text is not None:
                instructions.setdefault(harness, []).append(
                    InstructionPart(name, stealth.folder, text)
                )
        for harness in (h for h in shared if h in SETTINGS_HARNESSES):
            target = permission_target(stealth.manifest, harness, "permissions")
            if target is None:
                continue
            try:
                keys = setting_keys(harness, settings_fragment(target, stealth))
            except (LoadoutError, OSError) as error:
                refusals.append(
                    f"refused: stealth {name}: {error}; none of its {harness} settings render"
                )
                continue
            for path, value in keys:
                other = owners.get((harness, path))
                if other is not None:
                    refusals.append(setting_refusal(name, harness, path, manifest, other))
                    continue
                owners[(harness, path)] = name
                settings.setdefault(harness, []).append(PersonalSetting(name, path, value))
    return PersonalParts(
        {harness: tuple(parts) for harness, parts in instructions.items()},
        {harness: tuple(found) for harness, found in settings.items()},
        tuple(refusals),
    )


def opencode_instruction_paths(parts: PersonalParts) -> tuple[str, ...]:
    if not parts.instructions.get("opencode"):
        return ()
    from .stealth_render import OPENCODE_INSTRUCTIONS  # noqa: PLC0415 - it imports emit

    return tuple(
        str(part.folder / OPENCODE_INSTRUCTIONS) for part in parts.instructions.get("opencode", ())
    )


def _settings_document(
    found: Sequence[PersonalSetting], recorded: frozenset[str], *, held: bool
) -> Merged:
    kept = recorded if held else frozenset()
    document: dict[str, Any] = {}
    for setting in found:
        if key_name(setting.path) not in kept:
            document = with_path(document, setting.path, setting.value)
    owned = (frozenset(key_name(setting.path) for setting in found) | recorded) - kept
    return Merged(owned, json.dumps(document, indent=2, ensure_ascii=False), format="json")


def _codex_document(
    parts: PersonalParts, inputs: PersonalInputs, recorded: frozenset[str], *, held: bool
) -> Merged | None:
    from .stealth_renderers import render_codex_stealth_config  # noqa: PLC0415 - it imports emit

    kept = recorded if held else frozenset()
    found = parts.instructions.get("codex", ())
    instructions = (
        "\n\n".join(part.text for part in found)
        if found and "developer_instructions" not in kept
        else None
    )
    servers = {
        key: server
        for key, (_, server) in inputs.added.get("codex", {}).items()
        if key_name(("mcp_servers", key)) not in kept
    }
    if instructions is None and not servers and not kept:
        return None
    text, _ = render_codex_stealth_config(
        instructions, inputs.rules.get("codex", EMPTY_RULES), servers
    )
    owned = frozenset(
        {
            *(("developer_instructions",) if instructions is not None else ()),
            *(key_name(("mcp_servers", server)) for server in servers),
        }
    )
    return Merged((owned | recorded) - kept, text)


def personal_documents(
    root: Path,
    parts: PersonalParts,
    inputs: PersonalInputs,
    facts: Callable[[], TargetFacts],
    recorded: Mapping[Path, WrittenEntry],
) -> dict[Path, Output]:
    from .stealth_render import (  # noqa: PLC0415 - stealth_render imports emit
        NO_FACTS,
        STEALTH_PRESET,
        instruction_document,
    )

    def destination(harness: str, slice_name: str) -> Path:
        return root / str(STEALTH_PRESET[harness][slice_name].output)

    held = bool(inputs.report.held)
    outputs: dict[Path, Output] = {}
    for harness in ("claude", "droid", "pi"):
        if found := parts.instructions.get(harness):
            texts = tuple(part.text for part in found)
            known = facts() if harness == "claude" else NO_FACTS
            outputs[destination(harness, "instructions")] = instruction_document(
                harness, texts, known
            )
    for part in parts.instructions.get("cursor", ()):
        rule = destination("cursor", "instructions") / f"{part.source}.mdc"
        text = instruction_document("cursor", (part.text,), NO_FACTS)
        outputs[rule] = for_destination(rule, text)
    codex = destination("codex", "instructions")
    owned = recorded_keys(recorded.get(codex))
    if (config := _codex_document(parts, inputs, owned, held=held)) is not None:
        outputs[codex] = config
    for harness in SETTINGS_HARNESSES:
        path = destination(harness, "permissions")
        settings, owned = parts.settings.get(harness, ()), recorded_keys(recorded.get(path))
        if settings or (held and owned):
            outputs[path] = _settings_document(settings, owned, held=held)
    return outputs
