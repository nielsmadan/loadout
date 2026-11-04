from __future__ import annotations

import json
from pathlib import Path

from . import machine
from .errors import LoadoutError
from .overlay_manifest import load_overlay_manifest
from .overlay_registry import (
    NAME_PATTERN,
    RegisteredOverlay,
    load_registry,
    overlays_path,
    update_registry,
    with_overlay,
)

_RECORDS_DIR = "overlay-written"


def _record_holder(folder: Path, registered: set[str]) -> str | None:
    for record in sorted((machine.config_dir() / _RECORDS_DIR).glob("*.json")):
        try:
            data = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("overlay") == str(folder):
            name = data.get("name")
            if isinstance(name, str) and name not in registered:
                return name
    return None


def cmd_overlay_add(directory: Path | None, name: str | None) -> int:
    folder = (directory or Path.cwd()).resolve()
    load_overlay_manifest(folder)
    chosen = name if name is not None else folder.name.lower()
    if NAME_PATTERN.fullmatch(chosen) is None:
        raise LoadoutError(
            f"{chosen!r} is not a valid overlay name (lowercase letters, digits and "
            f"hyphens, starting with a letter or digit); pass --name"
        )
    registered = load_registry(overlays_path())
    if machine.is_registered(folder, machine.registration().source):
        raise LoadoutError(f"{folder} is this machine's registered global source, not an overlay")
    if (old := _record_holder(folder, {o.name for o in registered})) is not None and old != chosen:
        raise LoadoutError(
            f"{folder} has an unregistered overlay record named {old}; "
            f"run `loadout overlay remove {old}` first"
        )
    update_registry(lambda current: with_overlay(current, RegisteredOverlay(chosen, folder)))
    print(
        f"registered overlay {chosen} at {folder}; preview with `loadout check --overlay {chosen}`"
    )
    return 0


def _targets(overlay: RegisteredOverlay) -> str:
    if overlay.removing:
        return f"(removing: run `loadout overlay remove {overlay.name}`)"
    try:
        return ", ".join(load_overlay_manifest(overlay.folder).settings.targets)
    except (LoadoutError, OSError) as error:
        return f"(cannot load: {error})"


def cmd_overlay_list() -> int:
    registered = load_registry(overlays_path())
    if not registered:
        print("no overlays registered; add one with `loadout overlay add <folder>`")
        return 0
    for overlay in registered:
        print(f"{overlay.name}  {overlay.folder}  {_targets(overlay)}")
    return 0
