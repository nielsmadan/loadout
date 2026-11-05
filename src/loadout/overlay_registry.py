from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from . import machine
from .atomic import replace_atomically
from .errors import LoadoutError
from .overlay_lock import file_lock, same_path

OVERLAYS_FILE = "overlays.toml"
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_HEADER = (
    "# Overlays registered on this machine, managed by `loadout overlay add` and\n"
    "# `loadout overlay remove`.\n"
)
_ENTRY_KEYS = {"path", "removing"}


@dataclass(frozen=True)
class RegisteredOverlay:
    name: str
    folder: Path
    removing: bool = False


def overlays_path() -> Path:
    return machine.config_dir() / OVERLAYS_FILE


def _entry(name: str, raw: object, path: Path) -> RegisteredOverlay:
    if NAME_PATTERN.fullmatch(name) is None:
        raise LoadoutError(f"{path}: overlay name {name!r} must match {NAME_PATTERN.pattern}")
    if not isinstance(raw, dict):
        raise LoadoutError(f"{path}: [overlay.{name}] must be a table")
    if unknown := sorted(set(raw) - _ENTRY_KEYS):
        raise LoadoutError(f"{path}: [overlay.{name}] has unknown key {unknown[0]!r}")
    folder = raw.get("path")
    if not isinstance(folder, str) or not Path(folder).is_absolute():
        raise LoadoutError(f"{path}: [overlay.{name}] path must be an absolute path")
    removing = raw.get("removing", False)
    if not isinstance(removing, bool):
        raise LoadoutError(f"{path}: [overlay.{name}] removing must be true or false")
    return RegisteredOverlay(name, Path(folder), removing)


def parse_registry(text: str, path: Path) -> tuple[RegisteredOverlay, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: {error}") from None
    if unknown := sorted(set(data) - {"overlay"}):
        raise LoadoutError(f"{path}: unknown key {unknown[0]!r}")
    table = data.get("overlay", {})
    if not isinstance(table, dict):
        raise LoadoutError(f"{path}: overlay must be a table of overlays")
    overlays = tuple(_entry(name, raw, path) for name, raw in sorted(table.items()))
    folders = [overlay.folder for overlay in overlays]
    if duplicate := next((f for f in folders if folders.count(f) > 1), None):
        raise LoadoutError(f"{path}: the path {duplicate} is registered more than once")
    return overlays


class UnreadableRegistry(LoadoutError):
    pass


def load_registry(path: Path) -> tuple[RegisteredOverlay, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise UnreadableRegistry(f"{path}: {error}") from None
    except UnicodeDecodeError as error:
        raise LoadoutError(f"{path}: {error}") from None
    return parse_registry(text, path)


def _render_entry(overlay: RegisteredOverlay) -> str:
    lines = [
        f"[overlay.{overlay.name}]",
        f"path = {json.dumps(str(overlay.folder), ensure_ascii=False)}",
    ]
    return "\n".join([*lines, *(["removing = true"] if overlay.removing else [])]) + "\n"


def render_registry(overlays: Iterable[RegisteredOverlay]) -> str:
    entries = [_render_entry(overlay) for overlay in sorted(overlays, key=lambda o: o.name)]
    return _HEADER + "\n".join(entries)


def with_overlay(
    overlays: Sequence[RegisteredOverlay], added: RegisteredOverlay
) -> tuple[RegisteredOverlay, ...]:
    if any(o.name == added.name for o in overlays):
        raise LoadoutError(f"an overlay named {added.name} is already registered; pass --name")
    if (held := find_by_folder(overlays, added.folder)) is not None:
        raise LoadoutError(f"{added.folder} is already registered as the overlay {held.name}")
    return tuple(sorted((*overlays, added), key=lambda o: o.name))


def without_overlay(
    overlays: Sequence[RegisteredOverlay], name: str
) -> tuple[RegisteredOverlay, ...]:
    return tuple(o for o in overlays if o.name != name)


def marked_removing(
    overlays: Sequence[RegisteredOverlay], name: str
) -> tuple[RegisteredOverlay, ...]:
    return tuple(replace(o, removing=True) if o.name == name else o for o in overlays)


def find_by_folder(overlays: Sequence[RegisteredOverlay], folder: Path) -> RegisteredOverlay | None:
    return next((o for o in overlays if same_path(o.folder, folder)), None)


def update_registry(
    change: Callable[[tuple[RegisteredOverlay, ...]], tuple[RegisteredOverlay, ...]],
) -> tuple[RegisteredOverlay, ...]:
    path = overlays_path()
    with file_lock(path):
        updated = change(load_registry(path))
        text = render_registry(updated)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            replace_atomically(
                path, lambda handle: handle.write(text.encode("utf-8")), durable=True
            )
        except OSError as error:
            raise LoadoutError(f"{path}: {error}") from None
    return updated
