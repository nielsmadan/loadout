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
class Manifest:
    sources: tuple[Source, ...]
    targets: tuple[InstructionTarget, ...]


def _require(block: dict[str, object], key: str, agent: str) -> object:
    if key not in block:
        raise LoadoutError(f"instructions.{agent} is missing required key {key!r}")
    return block[key]


def _str_list(value: object, agent: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LoadoutError(f"instructions.{agent}: {key} must be a list of strings")
    return tuple(str(v) for v in value)


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

    raw_instructions = data.get("instructions", {})
    if not isinstance(raw_instructions, dict):
        raise LoadoutError(f"{path}: [instructions] must be a table")

    targets: list[InstructionTarget] = []
    for agent, block in sorted(raw_instructions.items()):
        if not isinstance(block, dict):
            raise LoadoutError(f"instructions.{agent} must be a table")
        output = _require(block, "output", agent)
        if not isinstance(output, str):
            raise LoadoutError(f"instructions.{agent}: output must be a string")
        out = PurePosixPath(output)
        if out.is_absolute() or not output or ".." in out.parts or out == PurePosixPath("."):
            raise LoadoutError(
                f"instructions.{agent}: output must be a relative path inside the repo "
                f"root, got {output!r}"
            )
        if any(t.path == out for t in targets):
            raise LoadoutError(
                f"instructions.{agent}: output {output!r} is already claimed by another target"
            )
        order = _str_list(_require(block, "order", agent), agent, "order")
        destinations = _str_list(block.get("destinations", []), agent, "destinations")
        profile = block.get("profile")
        if profile is not None and not isinstance(profile, str):
            raise LoadoutError(f"instructions.{agent}: profile must be a string")
        targets.append(
            InstructionTarget(
                path=out,
                fragments=order,
                destinations=tuple(PurePosixPath(d) for d in destinations),
                profile=profile,
            )
        )
    if not targets:
        raise LoadoutError(f"{path}: no [instructions.<agent>] targets declared")
    return Manifest(sources=sources, targets=tuple(targets))
