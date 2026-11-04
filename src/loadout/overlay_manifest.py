from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from . import overlay_seams
from .errors import LoadoutError
from .harnesses import KNOWN_HARNESSES
from .manifest import COMMON_BLOCK, MANIFEST_NAME, Manifest, PermissionTarget
from .sources import SourceParser, parse_sources

OVERLAY_TABLE = "overlay"

_SLICES = frozenset({"instructions", "mcp", "permissions", "skills"})

OVERLAY_SLICES: dict[str, frozenset[str]] = dict.fromkeys(KNOWN_HARNESSES, _SLICES)

OVERLAY_EXTRAS: dict[str, frozenset[str]] = {
    harness: frozenset(
        {"settings", "substitute"} if harness in ("claude", "droid") else {"substitute"}
    )
    for harness in KNOWN_HARNESSES
}

_OVERLAY_KEYS = ("targets", "follow_worktrees", "git_hook")

_NO_DESTINATION = "would need a destination the team's config does not merge, so an overlay cannot write it additively"

_REFUSED_KEYS = {
    "hooks": _NO_DESTINATION,
    "plugins": _NO_DESTINATION,
    "agents": _NO_DESTINATION,
    "module-config": _NO_DESTINATION,
    "defaults": _NO_DESTINATION,
    "preserve": "an overlay owns only the keys it writes",
    "profile": "an overlay has one profile, its loadout.toml",
}

_MCP_REFUSAL = (
    "servers are not picked by name; the overlay's sources supply them "
    "- remove the list, or set `mcp = false`"
)


@dataclass(frozen=True)
class OverlaySettings:
    targets: tuple[str, ...]
    follow_worktrees: bool = False
    git_hook: bool = False


@dataclass(frozen=True)
class OverlayManifest:
    folder: Path
    settings: OverlaySettings
    manifest: Manifest
    harnesses: tuple[str, ...]


def holds_overlay(folder: Path) -> bool:
    try:
        with (folder / MANIFEST_NAME).open("rb") as handle:
            return isinstance(tomllib.load(handle).get(OVERLAY_TABLE), dict)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False


def _parse_settings(raw: object, path: Path) -> OverlaySettings:
    if not isinstance(raw, dict):
        raise LoadoutError(f"{path}: [{OVERLAY_TABLE}] must be a table")
    stray = sorted(key for key in raw if key not in _OVERLAY_KEYS)
    if stray:
        raise LoadoutError(
            f"{path}: [{OVERLAY_TABLE}] has unknown key(s) {', '.join(stray)} "
            f"(allowed: {', '.join(_OVERLAY_KEYS)})"
        )
    targets = raw.get("targets")
    if (
        not isinstance(targets, list)
        or not targets
        or not all(isinstance(t, str) and t.startswith(("/", "~/")) for t in targets)
    ):
        raise LoadoutError(
            f"{path}: [{OVERLAY_TABLE}] targets must be a non-empty list of absolute or "
            f'"~/" patterns'
        )
    flags = {}
    for key in ("follow_worktrees", "git_hook"):
        value = raw.get(key, False)
        if not isinstance(value, bool):
            raise LoadoutError(f"{path}: [{OVERLAY_TABLE}] {key} must be true or false")
        if value:
            raise LoadoutError(
                f"{path}: [{OVERLAY_TABLE}] {key} arrives with worktree support; "
                f"set it to false or remove it"
            )
        flags[key] = value
    return OverlaySettings(targets=tuple(targets), **flags)


def _reason(harness: str | None, key: str) -> str:
    if key == "mcp-permissions" and harness in (None, "claude"):
        return "Claude's MCP approvals are in `permissions`"
    return _REFUSED_KEYS.get(key, "an overlay does not render it")


def _refuse_top_level(data: dict[str, object], path: Path) -> None:
    if "extends" in data or "remove" in data:
        raise LoadoutError(
            f"{path}: extends and remove are refused: an overlay has one profile, its loadout.toml"
        )
    stale = sorted(
        key for key in ("instructions", "permissions") if isinstance(data.get(key), dict)
    )
    if stale:
        raise LoadoutError(
            f"{path}: [{stale[0]}.*] is refused: an overlay writes only to its fixed "
            f"destinations; declare harness tables instead"
        )


def _refuse_key(path: Path, harness: str, key: str, value: object) -> None:
    allowed = OVERLAY_SLICES[harness] | OVERLAY_EXTRAS[harness]
    if key in allowed:
        if key == "mcp" and value is not False and value != []:
            raise LoadoutError(f"{path}: {harness}.mcp: {_MCP_REFUSAL}")
        return
    raise LoadoutError(f"{path}: {harness}.{key} is refused in an overlay: {_reason(harness, key)}")


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
        allowing = [h for h in declared if key in OVERLAY_SLICES[h] | OVERLAY_EXTRAS[h]]
        if not allowing:
            raise LoadoutError(
                f"{path}: [{COMMON_BLOCK}] {key} applies to no declared harness: "
                f"{_reason(None, key)}"
            )
        if key == "mcp" and value is not False and value != []:
            raise LoadoutError(f"{path}: [{COMMON_BLOCK}] mcp: {_MCP_REFUSAL}")
    rest = {k: v for k, v in data.items() if k != COMMON_BLOCK}
    return {
        **rest,
        **{
            harness: {
                **{
                    k: v
                    for k, v in common.items()
                    if k in OVERLAY_SLICES[harness] | OVERLAY_EXTRAS[harness]
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
            if _slice_of(t) in OVERLAY_SLICES.get(t.harness or "", ())
        ),
        module_config=(),
        agents=(),
    )


def load_overlay_manifest(
    folder: Path, *, sources: SourceParser = parse_sources
) -> OverlayManifest:
    path = folder / MANIFEST_NAME
    data = overlay_seams.read_toml(path)
    if OVERLAY_TABLE not in data:
        raise LoadoutError(f"{path}: no [{OVERLAY_TABLE}] table")
    settings = _parse_settings(data[OVERLAY_TABLE], path)
    rest = {k: v for k, v in data.items() if k != OVERLAY_TABLE}
    _refuse_top_level(rest, path)
    built = overlay_seams.build_manifest(_filtered_data(rest, path), path, sources=sources)
    return OverlayManifest(
        folder=folder,
        settings=settings,
        manifest=_restricted(built),
        harnesses=tuple(sorted(_declared(rest))),
    )
