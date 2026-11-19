from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .errors import LoadoutError
from .manifest import MANIFEST_NAME, Manifest, PermissionTarget
from .permissions.merge import merge_rules
from .permissions.renderers import MCP_NAMESPACE_HARNESS, refuse_mcp_namespace_overlap
from .permissions.rules import Rules
from .project import project_config_path, project_slices
from .servers import Server
from .stealth_git import GitFailure, identity
from .stealth_lock import same_path
from .stealth_registry import RegisteredStealth, load_registry, stealth_path

if TYPE_CHECKING:
    from .stealth_manifest import StealthManifest
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


def clash_refusal(
    source: str, kind: str, name: str, manifest: Path, other: str | None = None
) -> str:
    winner = "the team's" if other is None else f"stealth {other}'s"
    table = "skills" if kind == "skill" else "servers"
    local = (
        f" or name its folder {name}.local to replace the team's"
        if kind == "skill" and other is None
        else ""
    )
    return (
        f"refused: stealth {source}: its {kind} {name} clashes with {winner} {kind} {name}, "
        f'which wins; rename yours with [{table}.{name}] name = "<new>" in {manifest}{local}'
    )


def _probe(root: Path, homes: HarnessHomes) -> Target | SkippedTarget:
    from .stealth_targets import probe  # noqa: PLC0415 - stealth_targets imports emit

    return probe(root, homes, None)


def adopted_target(root: Path) -> bool:
    from .stealth_targets import Target, harness_homes  # noqa: PLC0415 - stealth_targets imports emit

    return project_config_path(root).is_file() and isinstance(_probe(root, harness_homes()), Target)


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


def personal_tier(root: Path) -> PersonalTier:
    from .stealth_targets import SkippedTarget, harness_homes  # noqa: PLC0415 - stealth_targets imports emit

    if not project_config_path(root).is_file():
        return NO_PERSONAL
    problem: str | None = None
    registered: tuple[RegisteredStealth, ...] = ()
    try:
        registered = load_registry(stealth_path())
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
                if server in team_servers:
                    refusals.append(clash_refusal(name, "server", server, manifest))
                elif server in owners:
                    refusals.append(clash_refusal(name, "server", server, manifest, owners[server]))
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
