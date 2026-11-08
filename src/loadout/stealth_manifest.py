from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import stealth_seams
from .errors import LoadoutError
from .harnesses import KNOWN_HARNESSES
from .manifest import (
    COMMON_BLOCK,
    MANIFEST_NAME,
    Manifest,
    PermissionTarget,
    renamed_overlay_table,
)
from .sources import SourceParser, parse_stealth_sources

STEALTH_TABLE = "stealth"

_SLICES = frozenset({"instructions", "mcp", "permissions", "skills"})

STEALTH_SLICES: dict[str, frozenset[str]] = dict.fromkeys(KNOWN_HARNESSES, _SLICES)

STEALTH_EXTRAS: dict[str, frozenset[str]] = {
    harness: frozenset(
        {"settings", "substitute"} if harness in ("claude", "droid") else {"substitute"}
    )
    for harness in KNOWN_HARNESSES
}

_STEALTH_KEYS = ("targets", "follow_worktrees", "git_hook")

_CONTROL = frozenset(map(chr, (*range(0x20), 0x7F)))

_NO_DESTINATION = "would need a destination the team's config does not merge, so a stealth source cannot write it additively"

_REFUSED_KEYS = {
    "hooks": _NO_DESTINATION,
    "plugins": _NO_DESTINATION,
    "agents": _NO_DESTINATION,
    "module-config": _NO_DESTINATION,
    "defaults": _NO_DESTINATION,
    "preserve": "a stealth source owns only the keys it writes",
    "profile": "a stealth source has one profile, its loadout.toml",
}

_MCP_REFUSAL = (
    "servers are not picked by name; the `[[source]]` entries supply them "
    "- remove the list, or set `mcp = false`"
)


@dataclass(frozen=True)
class StealthSettings:
    targets: tuple[str, ...]


@dataclass(frozen=True)
class StealthManifest:
    folder: Path
    settings: StealthSettings
    manifest: Manifest
    harnesses: tuple[str, ...]


def _lenient_data(folder: Path) -> dict[str, object]:
    try:
        with (folder / MANIFEST_NAME).open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def holds_stealth(folder: Path) -> bool:
    return isinstance(_lenient_data(folder).get(STEALTH_TABLE), dict)


def renamed_table(folder: Path) -> LoadoutError | None:
    return renamed_overlay_table(_lenient_data(folder), folder / MANIFEST_NAME)


def _storable(pattern: str) -> bool:
    try:
        pattern.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return not _CONTROL & set(pattern)


def _parse_settings(raw: object, path: Path) -> StealthSettings:
    if not isinstance(raw, dict):
        raise LoadoutError(f"{path}: [{STEALTH_TABLE}] must be a table")
    stray = sorted(key for key in raw if key not in _STEALTH_KEYS)
    if stray:
        raise LoadoutError(
            f"{path}: [{STEALTH_TABLE}] has unknown key(s) {', '.join(stray)} "
            f"(allowed: targets; follow_worktrees and git_hook are reserved)"
        )
    targets = raw.get("targets")
    if (
        not isinstance(targets, list)
        or not targets
        or not all(isinstance(t, str) and t.startswith(("/", "~/")) for t in targets)
    ):
        raise LoadoutError(
            f"{path}: [{STEALTH_TABLE}] targets must be a non-empty list of absolute or "
            f'"~/" patterns'
        )
    unstorable = next((t for t in targets if not _storable(t)), None)
    if unstorable is not None:
        raise LoadoutError(
            f"{path}: [{STEALTH_TABLE}] targets: the pattern {unstorable!r} holds a control "
            "character or a byte that is not UTF-8, which a target path cannot use"
        )
    for key in ("follow_worktrees", "git_hook"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise LoadoutError(f"{path}: [{STEALTH_TABLE}] {key} must be true or false")
        if value:
            raise LoadoutError(
                f"{path}: `[{STEALTH_TABLE}] {key}` is not available yet; it arrives in a later "
                "release — set it to false or remove it"
            )
    return StealthSettings(targets=tuple(targets))


def parse_targets(targets: list[str], path: Path) -> StealthSettings:
    return _parse_settings({"targets": targets}, path)


def _reason(harness: str | None, key: str) -> str:
    if key == "mcp-permissions" and harness in (None, "claude"):
        return "Claude's MCP approvals are in `permissions`"
    return _REFUSED_KEYS.get(key, "a stealth source does not render it")


def _refuse_top_level(data: dict[str, object], path: Path) -> None:
    if "extends" in data or "remove" in data:
        raise LoadoutError(
            f"{path}: extends and remove are refused: a stealth source has one profile, its loadout.toml"
        )
    stale = sorted(
        key for key in ("instructions", "permissions") if isinstance(data.get(key), dict)
    )
    if stale:
        raise LoadoutError(
            f"{path}: [{stale[0]}.*] is refused: a stealth source writes only to its fixed "
            f"destinations; declare harness tables instead"
        )


def _refuse_key(path: Path, harness: str, key: str, value: object) -> None:
    allowed = STEALTH_SLICES[harness] | STEALTH_EXTRAS[harness]
    if key in allowed:
        if key == "mcp" and value is not False:
            raise LoadoutError(f"{path}: {harness}.mcp: {_MCP_REFUSAL}")
        return
    raise LoadoutError(
        f"{path}: {harness}.{key} is refused in a stealth source: {_reason(harness, key)}"
    )


def _common_block(data: dict[str, object], path: Path) -> dict[str, object]:
    common = data.get(COMMON_BLOCK, {})
    if not isinstance(common, dict):
        raise LoadoutError(f"{path}: [{COMMON_BLOCK}] must be a table")
    return common


def _declared(data: dict[str, object]) -> dict[str, dict[str, object]]:
    return {h: b for h, b in data.items() if h in KNOWN_HARNESSES and isinstance(b, dict)}


def _filtered_data(data: dict[str, object], path: Path) -> dict[str, object]:
    declared = _declared(data)
    common = _common_block(data, path)
    for harness, block in declared.items():
        for key, value in block.items():
            _refuse_key(path, harness, key, value)
    for key, value in common.items():
        allowing = [h for h in declared if key in STEALTH_SLICES[h] | STEALTH_EXTRAS[h]]
        if not allowing:
            raise LoadoutError(
                f"{path}: [{COMMON_BLOCK}] {key} applies to no declared harness: "
                f"{_reason(None, key)}"
            )
        if key == "mcp" and value is not False:
            raise LoadoutError(f"{path}: [{COMMON_BLOCK}] mcp: {_MCP_REFUSAL}")
    rest = {k: v for k, v in data.items() if k != COMMON_BLOCK}
    return {
        **rest,
        **{
            harness: {
                **{
                    k: v
                    for k, v in common.items()
                    if k in STEALTH_SLICES[harness] | STEALTH_EXTRAS[harness]
                },
                **block,
            }
            for harness, block in declared.items()
        },
    }


def _slice_of(target: PermissionTarget) -> str:
    harness = target.harness or ""
    return "permissions" if target.name == harness else target.name.removeprefix(f"{harness}-")


def _restricted(manifest: Manifest) -> Manifest:
    return replace(
        manifest,
        permissions=tuple(
            t
            for t in manifest.permissions
            if _slice_of(t) in STEALTH_SLICES.get(t.harness or "", ())
        ),
        module_config=(),
        agents=(),
    )


def load_stealth_manifest(
    folder: Path, *, sources: SourceParser = parse_stealth_sources
) -> StealthManifest:
    path = folder / MANIFEST_NAME
    data = stealth_seams.read_toml(path)
    if (renamed := renamed_overlay_table(data, path)) is not None:
        raise renamed
    if STEALTH_TABLE not in data:
        raise LoadoutError(f"{path}: no [{STEALTH_TABLE}] table")
    settings = _parse_settings(data[STEALTH_TABLE], path)
    rest = {k: v for k, v in data.items() if k != STEALTH_TABLE}
    _refuse_top_level(rest, path)
    built = stealth_seams.build_manifest(_filtered_data(rest, path), path, sources=sources)
    return StealthManifest(
        folder=folder,
        settings=settings,
        manifest=_restricted(built),
        harnesses=tuple(sorted(_declared(rest))),
    )
