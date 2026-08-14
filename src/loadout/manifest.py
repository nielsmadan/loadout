from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .agents import GLOBAL_PRESET, known_agents
from .errors import LoadoutError
from .sources import Source, parse_sources

MANIFEST_NAME = "loadout.toml"

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


DEFAULT_PROFILE = "default"


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


def profile_path(root: Path, profile: str) -> Path:
    """`loadout.toml` is the default profile and the marker of a loadout root.

    Every other profile is a sibling beside it, so a source with one profile has
    one file and root detection is unchanged.
    """
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
    # Variant tags this profile wants, most specific first. A fragment resolves
    # to `<name>.<variant>` when that file exists and to `<name>` otherwise, so a
    # profile states the axis once instead of restating every slot that differs.
    variants: tuple[str, ...] = ()


def _require(block: dict[str, object], key: str, label: str) -> object:
    if key not in block:
        raise LoadoutError(f"{label} is missing required key {key!r}")
    return block[key]


def _str_list(value: object, label: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LoadoutError(f"{label}: {key} must be a list of strings")
    return tuple(str(v) for v in value)


def _expand_env(template: str, label: str) -> str:
    """Substitute `${VAR}` and `${VAR:-fallback}`.

    Which variable a harness reads is recorded in docs/reference/, never here.
    An empty value counts as unset, matching `machine_config_path`; an empty
    fallback does too, so `${VAR:-}` cannot quietly resolve to nothing.
    """

    def substitute(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        value = os.environ.get(name)
        if value:
            return value
        if fallback:
            return fallback
        raise LoadoutError(
            f"{label}: destination {template!r} reads ${{{name}}}, which is unset or "
            f"empty, and has no fallback; give it one as ${{{name}:-<path>}}"
        )

    expanded = _ENV_REFERENCE.sub(substitute, template)
    # Anything brace-shaped left over is a reference this grammar does not cover
    # (`${VAR-x}`, `${VAR:?x}`, `${}`, an unclosed brace, a nested fallback). Left
    # alone it would become literal path text and be written to as a directory. A
    # stray `}` is the tell for the nested case, where the substitution succeeded
    # and only the inner reference's closing brace survives.
    if "${" in expanded or "}" in expanded:
        raise LoadoutError(
            f"{label}: destination {template!r} contains a reference loadout does not "
            f"understand; only ${{VAR}} and ${{VAR:-fallback}} are substituted"
        )
    return expanded


def resolve_destination(template: str, label: str) -> Path:
    """Resolve a destination template to the machine path `sync` will write.

    Deferred to render time rather than parse time for two reasons: a target the
    active profile does not select must not be able to fail the run over a variable
    this machine has no reason to set, and `~` resolves here too, so the checks
    below see the whole path rather than half of one."""
    expanded = _expand_env(template, label)
    try:
        path = Path(expanded).expanduser()
    except RuntimeError as error:
        raise LoadoutError(f"{label}: destination {template!r}: {error}") from error
    if not path.is_absolute() or ".." in path.parts:
        raise LoadoutError(
            f"{label}: destination {template!r} resolves to {str(path)!r}, which must be "
            f"an absolute path with no '..' components"
        )
    return path


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
            )
        )
    return tuple(targets)


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


def _resolve_extends(root: Path, profile: str) -> dict[str, object]:
    """Flatten a profile onto the one it extends, so it states only deltas.

    Targets override by name and wholesale, not key by key: a profile that
    changes a target restates it. Deep-merging targets would make it ambiguous
    whether an omitted `order` means "inherit" or "empty".
    """
    seen: list[str] = []
    chain: list[dict[str, object]] = []
    current = profile
    while True:
        if current in seen:
            cycle = " -> ".join([*seen, current])
            raise LoadoutError(f"profile extends cycle: {cycle}")
        seen.append(current)
        data = _read_toml(profile_path(root, current))
        chain.append(data)
        parent = data.get("extends")
        if parent is None:
            break
        if not isinstance(parent, str):
            raise LoadoutError(f"{profile_path(root, current)}: extends must be a string")
        current = parent

    merged: dict[str, object] = {}
    for data in reversed(chain):
        for key, value in data.items():
            if key == "extends":
                continue
            existing = merged.get(key)
            if key in ("instructions", "permissions") and isinstance(existing, dict):
                assert isinstance(value, dict)
                merged[key] = {**existing, **value}
            else:
                merged[key] = value
    return merged


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
        return load_manifest(manifest_path(root))
    return _build_manifest(_resolve_extends(root, profile), path)


def load_manifest(path: Path) -> Manifest:
    return _build_manifest(_read_toml(path), path)


RESERVED_KEYS = frozenset({"source", "instructions", "permissions", "extends", "variants"})

# permissions and mcp render with no authoring decision to make, so an agent
# block that names neither still gets them. instructions and settings must be
# named: instructions need an `order` (spec 1 §7 — alphabetical demonstrably
# fails), and settings names an input rather than an output.
AUTOMATIC_SLICES = ("permissions", "mcp")


def _agent_slice_names(agent: str, block: dict[str, object]) -> list[str]:
    offered = GLOBAL_PRESET[agent]
    named = [k for k in block if k in offered]
    automatic = [s for s in AUTOMATIC_SLICES if s in offered and s not in block]
    return named + automatic


def _parse_agents(
    data: dict[str, object], path: Path, claimed: set[PurePosixPath]
) -> tuple[tuple[InstructionTarget, ...], tuple[PermissionTarget, ...]]:
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

    targets: list[InstructionTarget] = []
    permissions: list[PermissionTarget] = []
    for agent in sorted(known_agents()):
        block = data.get(agent)
        if block is None:
            continue
        if not isinstance(block, dict):
            raise LoadoutError(f"{path}: [{agent}] must be a table")
        offered = GLOBAL_PRESET[agent]
        stray = sorted(k for k in block if k not in offered and k not in ("settings", "preserve"))
        if stray:
            raise LoadoutError(
                f"{agent}: unknown slice(s) {', '.join(stray)} "
                f"(this agent offers: {', '.join(sorted(offered))})"
            )
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
            if slice_name == "instructions":
                targets.append(
                    InstructionTarget(
                        path=out,
                        fragments=tuple(_str_list(block[slice_name], label, "instructions")),
                        destinations=destinations,
                        name=agent,
                    )
                )
                continue
            raw_select = block.get(slice_name)
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
                    preserve=tuple(_str_list(block.get("preserve", []), label, "preserve")),
                    select_all=raw_select != [],
                    destinations=destinations,
                )
            )
    return tuple(targets), tuple(permissions)


def _build_manifest(data: dict[str, object], path: Path) -> Manifest:
    raw_sources = data.get("source")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise LoadoutError(f"{path}: at least one [[source]] entry is required")
    sources = parse_sources(list(raw_sources), path.parent)

    claimed: set[PurePosixPath] = set()
    targets = _parse_instructions(data.get("instructions", {}), path, claimed)
    permissions = _parse_permissions(data.get("permissions", {}), path, claimed)
    agent_targets, agent_permissions = _parse_agents(data, path, claimed)
    targets += agent_targets
    permissions += agent_permissions

    for target in permissions:
        if target.base is not None and target.base in claimed:
            raise LoadoutError(
                f"permissions.{target.name}: base {str(target.base)!r} is a generated "
                f"output; a base must be an input, never something loadout writes"
            )

    variants = _str_list(data.get("variants", []), str(path), "variants")

    if not targets and not permissions:
        raise LoadoutError(
            f"{path}: no [<agent>], [instructions.<agent>] or [permissions.<name>] targets declared"
        )
    return Manifest(sources=sources, targets=targets, permissions=permissions, variants=variants)
