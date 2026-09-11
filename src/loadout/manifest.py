from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .agents import GLOBAL_PRESET, known_agents
from .artifacts import Artifacts, artifact_reference
from .destinations import resolve_destination
from .errors import LoadoutError
from .sources import Source, parse_sources
from .toml_paths import contains, key_path

MANIFEST_NAME = "loadout.toml"
__all__ = ["resolve_destination"]

DEFAULT_PROFILE = "default"


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def profile_path(root: Path, profile: str) -> Path:
    """`loadout.toml` is the default profile and the marker of a loadout root.

    Every other profile is a sibling beside it, so a source with one profile has
    one file and root detection is unchanged.
    """
    if not profile or profile in {".", ".."} or "/" in profile or "\\" in profile:
        raise LoadoutError(f"profile name must name a sibling file: {profile!r}")
    if profile == DEFAULT_PROFILE:
        return manifest_path(root)
    return root / f"{profile}.toml"


def declared_profile_files(root: Path) -> set[str]:
    return {
        p.stem
        for p in root.glob("*.toml")
        if p.name != MANIFEST_NAME and "extends" in _read_toml(p)
    }


@dataclass(frozen=True)
class InstructionTarget:
    """One generated instruction file, its fragment order, and where it deploys.

    `path` is the in-repo output location; it is None when the target only
    deploys to `destinations` (see `_output_path`)."""

    path: PurePosixPath | None
    fragments: tuple[str, ...]
    destinations: tuple[PurePosixPath, ...]
    name: str = ""
    profile: str | None = None
    # Fragment swaps this target applies, declared in the manifest rather than
    # inferred from a filename: {"git-policy": "git-policy.autonomous"}. Lets a
    # profile change two entries of a ten-entry order without restating it.
    substitute: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SkillsTarget:
    """Which agent gets the skill trees, and the directory they land in.

    A skill is discovered rather than named, so this carries no list: the
    manifest decides *whether* an agent gets skills, and the source directory
    decides *which*.
    """

    agent: str
    destinations: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class ModuleConfigTarget:
    """Which agent gets the module-config tree, and the root it lands under.

    Carries no list for the same reason `SkillsTarget` does not: the relative
    path inside the source tree is the declaration.
    """

    agent: str
    destinations: tuple[PurePosixPath, ...]


@dataclass(frozen=True)
class PermissionTarget:
    """One generated permission file, its renderer, and its base document.

    `path` is the in-repo output location; it is None when the target only
    deploys to `destinations` (see `_output_path`).

    `base` names a file; `settings` names fragments of the settings slice that
    compose into the same document. They are two spellings of one input and
    cannot both be given."""

    name: str
    path: PurePosixPath | None
    renderer: str
    base: PurePosixPath | None = None
    settings: tuple[str, ...] = ()
    # This slice's own fragments, when it contributes one key rather than
    # transforming the document. Distinct from `settings`, which is the residual
    # the whole file starts from and is the same for every slice of an agent.
    content: tuple[str, ...] = ()
    # Set when this slice contributes one key's value rather than transforming
    # the whole document.
    owned_key: str | None = None
    content_slice: str | None = None
    preserve: tuple[str, ...] = ()
    select_all: bool = True
    profile: str | None = None
    destinations: tuple[PurePosixPath, ...] = ()
    # Which agent owns this target, when it came from an agent block. Several of
    # one agent's slices may write one file and compose into it; two *different*
    # owners naming one path is still a collision.
    agent: str | None = None


@dataclass(frozen=True)
class Manifest:
    sources: tuple[Source, ...]
    targets: tuple[InstructionTarget, ...]
    permissions: tuple[PermissionTarget, ...] = ()
    skills: tuple[SkillsTarget, ...] = ()
    module_config: tuple[ModuleConfigTarget, ...] = ()
    artifacts: Artifacts | None = None
    config_paths: tuple[Path, ...] = ()


def _require(block: dict[str, object], key: str, label: str) -> object:
    if key not in block:
        raise LoadoutError(f"{label} is missing required key {key!r}")
    return block[key]


def _str_list(value: object, label: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LoadoutError(f"{label}: {key} must be a list of strings")
    return tuple(str(v) for v in value)


def _destinations(value: object, label: str) -> tuple[PurePosixPath, ...]:
    """A destination is a template, resolved per render by `resolve_destination`.
    Only its machine-independent shape can be checked here."""
    raw = _str_list(value, label, "destinations")
    for entry in raw:
        if not entry:
            raise LoadoutError(f"{label}: destination must be a non-empty path")
    return tuple(PurePosixPath(entry) for entry in raw)


def _output_path(
    output: object,
    label: str,
    claimed: set[PurePosixPath],
    has_destinations: bool,
) -> PurePosixPath | None:
    if output is None:
        if not has_destinations:
            raise LoadoutError(
                f"{label}: must declare 'output', a non-empty 'destinations', or both — "
                f"otherwise it generates nothing"
            )
        return None
    if not isinstance(output, str):
        raise LoadoutError(f"{label}: output must be a string")
    out = PurePosixPath(output)
    if out.is_absolute() or not output or ".." in out.parts or out == PurePosixPath("."):
        raise LoadoutError(
            f"{label}: output must be a relative path inside the repo root, got {output!r}"
        )
    if out in claimed:
        raise LoadoutError(f"{label}: output {output!r} is already claimed by another target")
    claimed.add(out)
    return out


def _parse_instructions(
    raw_instructions: object, path: Path, claimed: set[PurePosixPath]
) -> tuple[InstructionTarget, ...]:
    if not isinstance(raw_instructions, dict):
        raise LoadoutError(f"{path}: [instructions] must be a table")

    targets: list[InstructionTarget] = []
    for agent, block in sorted(raw_instructions.items()):
        if not isinstance(block, dict):
            raise LoadoutError(f"instructions.{agent} must be a table")
        label = f"instructions.{agent}"
        destinations = _destinations(block.get("destinations", []), label)
        out = _output_path(block.get("output"), label, claimed, bool(destinations))
        order = _str_list(_require(block, "order", agent), label, "order")
        profile = block.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise LoadoutError(f"instructions.{agent}: profile must be a string")
        targets.append(
            InstructionTarget(
                path=out,
                fragments=order,
                destinations=destinations,
                name=agent,
                profile=profile,
                substitute=_parse_substitute(block.get("substitute"), label),
            )
        )
    return tuple(targets)


def _parse_substitute(raw: object, label: str) -> tuple[tuple[str, str], ...]:
    """Fragment swaps, `{from = "to"}`.

    Declared here rather than encoded in filenames: a name like
    `git-policy.autonomous` is then just a name, and nothing has to infer which
    part of it is a variant tag.
    """
    if raw is None:
        return ()
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise LoadoutError(f"{label}: substitute must be a table of fragment -> fragment")
    return tuple((str(k), v) for k, v in raw.items())


def _parse_settings(raw: object, label: str, base: PurePosixPath | None) -> tuple[str, ...]:
    """Fragment names of the settings slice — one input, two spellings.

    A string is the single-fragment case; a list composes in order. `base` names
    the same document by path, so giving both is ambiguous rather than additive.
    """
    if raw is None:
        settings: tuple[str, ...] = ()
    elif isinstance(raw, str):
        settings = (raw,)
    elif isinstance(raw, list) and all(isinstance(v, str) for v in raw):
        settings = tuple(raw)
    else:
        raise LoadoutError(f"{label}: settings must be a string or a list of strings")
    if settings and base is not None:
        raise LoadoutError(
            f"{label}: base and settings are two spellings of the same input; give one. "
            f"`settings` names fragments of the settings slice, `base` names a file path."
        )
    return settings


def _parse_permissions(
    raw_permissions: object, path: Path, claimed: set[PurePosixPath]
) -> tuple[PermissionTarget, ...]:
    if not isinstance(raw_permissions, dict):
        raise LoadoutError(f"{path}: [permissions] must be a table")

    permissions: list[PermissionTarget] = []
    for name, block in sorted(raw_permissions.items()):
        label = f"permissions.{name}"
        if not isinstance(block, dict):
            raise LoadoutError(f"{label} must be a table")
        destinations = _destinations(block.get("destinations", []), label)
        out = _output_path(block.get("output"), label, claimed, bool(destinations))

        renderer = block.get("render")
        if not isinstance(renderer, str) or not renderer:
            raise LoadoutError(f"{label}: render must be a non-empty string")

        raw_base = block.get("base")
        if raw_base is not None and not isinstance(raw_base, str):
            raise LoadoutError(f"{label}: base must be a string")
        base = PurePosixPath(raw_base) if raw_base else None
        if base is not None and (base.is_absolute() or ".." in base.parts):
            raise LoadoutError(f"{label}: base must be a relative path inside the repo root")

        settings = _parse_settings(block.get("settings"), label, base)

        raw_preserve = block.get("preserve", [])
        if not isinstance(raw_preserve, list) or not all(isinstance(v, str) for v in raw_preserve):
            raise LoadoutError(f"{label}: preserve must be a list of strings")

        select_all = True
        if "rules" in block:
            raw_rules = block["rules"]
            if raw_rules != []:
                raise LoadoutError(
                    f"{label}: rules only supports [] (select nothing) in milestone 3; "
                    f"named rule-set selection arrives in milestone 4"
                )
            select_all = False

        profile = block.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise LoadoutError(f"{label}: profile must be a string")

        permissions.append(
            PermissionTarget(
                name=name,
                path=out,
                renderer=renderer,
                base=base,
                settings=settings,
                preserve=tuple(raw_preserve),
                select_all=select_all,
                profile=profile,
                destinations=destinations,
            )
        )
    return tuple(permissions)


def _read_toml(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise LoadoutError(f"manifest not found: {path}")
    try:
        with path.open("rb") as handle:
            data: dict[str, object] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error
    return data


def _remove_inherited(merged: dict[str, object], data: dict[str, object]) -> None:
    if "remove" not in data:
        return
    if "extends" not in data:
        raise LoadoutError("profile remove requires extends")
    names = _str_list(data["remove"], "profile", "remove")
    paths = [key_path(name) for name in names]
    for index, path in enumerate(paths):
        if any(contains(other, path) or contains(path, other) for other in paths[:index]):
            raise LoadoutError(f"profile remove paths overlap: {names[index]!r}")
        parent = merged
        for key in path[:-1]:
            if key not in parent:
                raise LoadoutError(f"profile remove path does not exist: {names[index]!r}")
            child = parent[key]
            if not isinstance(child, dict):
                raise LoadoutError(f"profile remove path must traverse tables: {names[index]!r}")
            parent = child
        if path[-1] not in parent:
            raise LoadoutError(f"profile remove path does not exist: {names[index]!r}")
        del parent[path[-1]]


def _inherit_fields(existing: object, value: object) -> object:
    if isinstance(existing, dict) and isinstance(value, dict):
        return {**existing, **copy.deepcopy(value)}
    return copy.deepcopy(value)


def _resolve_extends(path: Path) -> tuple[dict[str, object], tuple[Path, ...]]:
    """Flatten a profile onto the one it extends, so it states only deltas.

    Blocks merge **per key**, so a profile naming one key inherits the rest —
    otherwise adding a `substitute` would mean restating the instruction order
    it exists to avoid restating. Absent means inherit; an explicit `[]` means
    empty, which is the convention `permissions = []` already set.
    """
    seen: list[Path] = []
    chain: list[dict[str, object]] = []
    root = path.parent.resolve()
    current = path.resolve()
    while True:
        if current.parent != root:
            raise LoadoutError(f"profile manifest escapes source root {root}: {current}")
        if current in seen:
            cycle = " -> ".join(
                DEFAULT_PROFILE if p.name == MANIFEST_NAME else p.stem for p in [*seen, current]
            )
            raise LoadoutError(f"profile extends cycle: {cycle}")
        seen.append(current)
        data = _read_toml(current)
        chain.append(data)
        parent = data.get("extends")
        if parent is None:
            break
        if not isinstance(parent, str):
            raise LoadoutError(f"{current}: extends must be a string")
        current = profile_path(root, parent).resolve()

    merged: dict[str, object] = {}
    for data in reversed(chain):
        _remove_inherited(merged, data)
        for key, value in data.items():
            if key in {"extends", "remove"}:
                continue
            existing = merged.get(key)
            if key in {"instructions", "permissions"} and isinstance(value, dict):
                targets = dict(existing) if isinstance(existing, dict) else {}
                for name, fields in value.items():
                    targets[name] = _inherit_fields(targets.get(name), fields)
                merged[key] = targets
            else:
                merged[key] = _inherit_fields(existing, value)
    return merged, tuple(seen)


def load_profile(root: Path, profile: str = DEFAULT_PROFILE) -> Manifest:
    """The manifest for one profile.

    A profile is a file — `loadout.toml` for the default, `<profile>.toml`
    beside it otherwise. When that file is absent the profile is declared the
    older way, with `profile = "<name>"` on individual targets inside
    `loadout.toml`, so fall back to it and let target selection do the work.
    Both spellings parse during the transition.
    """
    path = profile_path(root, profile)
    if not path.is_file():
        path = manifest_path(root)
    return load_manifest(path)


def load_manifest(path: Path) -> Manifest:
    data, config_paths = _resolve_extends(path)
    return _build_manifest(data, path, config_paths=config_paths)


COMMON_BLOCK = "all"

RESERVED_KEYS = frozenset(
    {"source", "instructions", "permissions", "extends", "remove", "artifacts", COMMON_BLOCK}
)

# permissions, mcp-permissions, skills and module-config render with no authoring
# decision to make, so an agent block that names none of them still gets them. For
# skills and module-config the directory is the declaration (spec 4a §2): dropping a
# tree in renders it everywhere. instructions and settings must be
# named: instructions need an `order` (spec 1 §7 — alphabetical demonstrably
# fails), and settings names an input rather than an output.
AUTOMATIC_SLICES = ("permissions", "mcp-permissions", "mcp", "skills", "module-config")


def _agent_slice_names(agent: str, block: dict[str, object]) -> list[str]:
    """Which slices this agent renders: what it names, plus the automatic ones.

    `<slice> = false` switches an automatic slice off. It is the only way to say
    "not this one" — an absent key means *automatic*, and `[]` already means
    "render with no rules selected", which is a different thing that renders a
    file. Needed when a harness stops reading what loadout writes: the renderer
    is still a capability, so it stays, and the manifest is where one machine
    says it has no use for it.
    """
    offered = GLOBAL_PRESET[agent]
    disabled = {k for k, v in block.items() if v is False}
    named = [k for k in block if k in offered and k not in disabled]
    automatic = [s for s in AUTOMATIC_SLICES if s in offered and s not in block]
    return named + automatic


def _parse_agents(
    data: dict[str, object], path: Path, claimed: set[PurePosixPath]
) -> tuple[
    tuple[InstructionTarget, ...],
    tuple[PermissionTarget, ...],
    tuple[SkillsTarget, ...],
    tuple[ModuleConfigTarget, ...],
]:
    """Agent-keyed blocks: `[claude]` with slices beneath it.

    Each slice becomes the same target the older spelling declares by hand; the
    preset supplies the renderer and destination so a manifest never repeats a
    machine path. Both spellings coexist during the transition.
    """
    unknown = sorted(
        k
        for k, v in data.items()
        if isinstance(v, dict) and k not in RESERVED_KEYS and k not in known_agents()
    )
    if unknown:
        known = ", ".join(sorted(known_agents()))
        raise LoadoutError(f"{path}: unknown agent(s) {', '.join(unknown)} (known: {known})")

    common = data.get(COMMON_BLOCK, {})
    if not isinstance(common, dict):
        raise LoadoutError(f"{path}: [{COMMON_BLOCK}] must be a table")

    targets: list[InstructionTarget] = []
    permissions: list[PermissionTarget] = []
    skills: list[SkillsTarget] = []
    module_config: list[ModuleConfigTarget] = []
    for agent in sorted(known_agents()):
        declared = data.get(agent)
        if declared is None:
            # [all] supplies defaults to the agents you named; it does not name
            # them. Otherwise declaring it would silently enable every harness.
            continue
        if not isinstance(declared, dict):
            raise LoadoutError(f"{path}: [{agent}] must be a table")
        block = {**common, **declared}
        offered = GLOBAL_PRESET[agent]
        # Only what the agent block itself names is checked. A key from [all]
        # that this agent has no slice for is simply not for it — [all] is a
        # default, so it applies where it applies. An unknown key the agent
        # named is still a typo worth catching.
        extras = ("settings", "preserve", "substitute")
        stray = sorted(k for k in declared if k not in offered and k not in extras)
        if stray:
            raise LoadoutError(
                f"{agent}: unknown slice(s) {', '.join(stray)} "
                f"(this agent offers: {', '.join(sorted(offered))})"
            )
        block = {k: v for k, v in block.items() if k in offered or k in extras}
        for slice_name in _agent_slice_names(agent, block):
            spec = offered[slice_name]
            label = f"{agent}.{slice_name}"
            destinations = (
                (PurePosixPath(spec.destination),) if spec.destination is not None else ()
            )
            out = PurePosixPath(spec.output) if spec.output is not None else None
            if out is not None:
                if out in claimed:
                    raise LoadoutError(f"{label}: output {str(out)!r} is already claimed")
                claimed.add(out)
            if slice_name == "skills":
                skills.append(SkillsTarget(agent=agent, destinations=destinations))
                continue
            if slice_name == "module-config":
                module_config.append(ModuleConfigTarget(agent=agent, destinations=destinations))
                continue
            if slice_name == "instructions":
                targets.append(
                    InstructionTarget(
                        path=out,
                        fragments=tuple(_str_list(block[slice_name], label, "instructions")),
                        destinations=destinations,
                        name=agent,
                        substitute=_parse_substitute(block.get("substitute"), label),
                    )
                )
                continue
            raw_select = block.get(slice_name)
            preserve = tuple(
                dict.fromkeys(
                    (*spec.preserve, *_str_list(block.get("preserve", []), label, "preserve"))
                )
            )
            permissions.append(
                PermissionTarget(
                    agent=agent,
                    name=agent if slice_name == "permissions" else f"{agent}-{slice_name}",
                    path=out,
                    renderer=spec.renderer or "",
                    settings=_parse_settings(block.get("settings"), label, None),
                    content=(
                        _parse_settings(block.get(spec.source_slice), label, None)
                        if spec.source_slice is not None
                        else ()
                    ),
                    owned_key=spec.owned_key,
                    content_slice=spec.source_slice,
                    preserve=preserve,
                    select_all=raw_select != [],
                    destinations=destinations,
                )
            )
    return tuple(targets), tuple(permissions), tuple(skills), tuple(module_config)


def _build_manifest(
    data: dict[str, object], path: Path, *, config_paths: tuple[Path, ...] = ()
) -> Manifest:
    artifacts = (
        artifact_reference(data["artifacts"], path, "global") if "artifacts" in data else None
    )
    raw_sources = data.get("source", [] if artifacts is not None else None)
    if not isinstance(raw_sources, list) or (not raw_sources and artifacts is None):
        raise LoadoutError(f"{path}: at least one [[source]] entry is required")
    sources = parse_sources(list(raw_sources), path.parent)

    claimed: set[PurePosixPath] = set()
    targets = _parse_instructions(data.get("instructions", {}), path, claimed)
    permissions = _parse_permissions(data.get("permissions", {}), path, claimed)
    agent_targets, agent_permissions, agent_skills, agent_module_config = _parse_agents(
        data, path, claimed
    )
    targets += agent_targets
    permissions += agent_permissions

    for target in permissions:
        if target.base is not None and target.base in claimed:
            raise LoadoutError(
                f"permissions.{target.name}: base {str(target.base)!r} is a generated "
                f"output; a base must be an input, never something loadout writes"
            )

    if not targets and not permissions and artifacts is None:
        raise LoadoutError(
            f"{path}: no [<agent>], [instructions.<agent>] or [permissions.<name>] targets declared"
        )
    return Manifest(
        sources=sources,
        targets=targets,
        permissions=permissions,
        skills=agent_skills,
        module_config=agent_module_config,
        artifacts=artifacts,
        config_paths=config_paths or (path,),
    )
