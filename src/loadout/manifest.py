from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LoadoutError
from .sources import Source, parse_sources

MANIFEST_NAME = "loadout.toml"

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


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
    preserve: tuple[str, ...] = ()
    select_all: bool = True
    profile: str | None = None
    destinations: tuple[PurePosixPath, ...] = ()


@dataclass(frozen=True)
class Manifest:
    sources: tuple[Source, ...]
    targets: tuple[InstructionTarget, ...]
    permissions: tuple[PermissionTarget, ...] = ()


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


def load_manifest(path: Path) -> Manifest:
    if not path.is_file():
        raise LoadoutError(f"manifest not found: {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error

    raw_sources = data.get("source")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise LoadoutError(f"{path}: at least one [[source]] entry is required")
    sources = parse_sources(list(raw_sources), path.parent)

    claimed: set[PurePosixPath] = set()
    targets = _parse_instructions(data.get("instructions", {}), path, claimed)
    permissions = _parse_permissions(data.get("permissions", {}), path, claimed)

    for target in permissions:
        if target.base is not None and target.base in claimed:
            raise LoadoutError(
                f"permissions.{target.name}: base {str(target.base)!r} is a generated "
                f"output; a base must be an input, never something loadout writes"
            )

    if not targets and not permissions:
        raise LoadoutError(
            f"{path}: no [instructions.<agent>] or [permissions.<name>] targets declared"
        )
    return Manifest(sources=sources, targets=targets, permissions=permissions)
