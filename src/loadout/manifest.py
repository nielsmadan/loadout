from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LoadoutError
from .sources import Source, parse_sources

MANIFEST_NAME = "loadout.toml"


def manifest_path(root: Path) -> Path:
    return root / MANIFEST_NAME


@dataclass(frozen=True)
class InstructionTarget:
    """One generated instruction file, its fragment order, and where it deploys."""

    path: PurePosixPath
    fragments: tuple[str, ...]
    destinations: tuple[PurePosixPath, ...]
    profile: str | None = None


@dataclass(frozen=True)
class PermissionTarget:
    """One generated permission file, its renderer, and its base document."""

    name: str
    path: PurePosixPath
    renderer: str
    base: PurePosixPath | None = None
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


def _destinations(value: object, label: str) -> tuple[PurePosixPath, ...]:
    """Unlike output, a destination is legitimately absolute (it's a real machine path,
    typically under `~`) — only reject an empty string or a '..' component."""
    raw = _str_list(value, label, "destinations")
    result: list[PurePosixPath] = []
    for entry in raw:
        dest = PurePosixPath(entry)
        if not entry or ".." in dest.parts:
            raise LoadoutError(
                f"{label}: destination {entry!r} must be a non-empty path with no '..' components"
            )
        result.append(dest)
    return tuple(result)


def _output_path(output: object, label: str, claimed: set[PurePosixPath]) -> PurePosixPath:
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
        out = _output_path(_require(block, "output", label), label, claimed)
        order = _str_list(_require(block, "order", agent), label, "order")
        destinations = _destinations(block.get("destinations", []), label)
        profile = block.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise LoadoutError(f"instructions.{agent}: profile must be a string")
        targets.append(
            InstructionTarget(
                path=out,
                fragments=order,
                destinations=destinations,
                profile=profile,
            )
        )
    return tuple(targets)


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
        out = _output_path(_require(block, "output", label), label, claimed)

        renderer = block.get("render")
        if not isinstance(renderer, str) or not renderer:
            raise LoadoutError(f"{label}: render must be a non-empty string")

        raw_base = block.get("base")
        if raw_base is not None and not isinstance(raw_base, str):
            raise LoadoutError(f"{label}: base must be a string")
        base = PurePosixPath(raw_base) if raw_base else None
        if base is not None and (base.is_absolute() or ".." in base.parts):
            raise LoadoutError(f"{label}: base must be a relative path inside the repo root")

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

        destinations = _destinations(block.get("destinations", []), label)

        permissions.append(
            PermissionTarget(
                name=name,
                path=out,
                renderer=renderer,
                base=base,
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
