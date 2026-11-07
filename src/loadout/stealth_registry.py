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
from .stealth_lock import file_lock, same_path

STEALTH_FILE = "stealth.toml"
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_HEADER = (
    "# Stealth sources registered on this machine, managed by `loadout stealth add` and\n"
    "# `loadout stealth remove`.\n"
)
_ENTRY_KEYS = {"path", "removing"}


@dataclass(frozen=True)
class RegisteredStealth:
    name: str
    folder: Path
    removing: bool = False


def stealth_path() -> Path:
    return machine.config_dir() / STEALTH_FILE


def _entry(name: str, raw: object, path: Path) -> RegisteredStealth:
    if NAME_PATTERN.fullmatch(name) is None:
        raise LoadoutError(f"{path}: stealth name {name!r} must match {NAME_PATTERN.pattern}")
    if not isinstance(raw, dict):
        raise LoadoutError(f"{path}: [stealth.{name}] must be a table")
    if unknown := sorted(set(raw) - _ENTRY_KEYS):
        raise LoadoutError(f"{path}: [stealth.{name}] has unknown key {unknown[0]!r}")
    folder = raw.get("path")
    if not isinstance(folder, str) or not Path(folder).is_absolute():
        raise LoadoutError(f"{path}: [stealth.{name}] path must be an absolute path")
    removing = raw.get("removing", False)
    if not isinstance(removing, bool):
        raise LoadoutError(f"{path}: [stealth.{name}] removing must be true or false")
    return RegisteredStealth(name, Path(folder), removing)


def parse_registry(text: str, path: Path) -> tuple[RegisteredStealth, ...]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: {error}") from None
    if unknown := sorted(set(data) - {"stealth"}):
        raise LoadoutError(f"{path}: unknown key {unknown[0]!r}")
    table = data.get("stealth", {})
    if not isinstance(table, dict):
        raise LoadoutError(f"{path}: stealth must be a table of stealth sources")
    stealth_sources = tuple(_entry(name, raw, path) for name, raw in sorted(table.items()))
    folders = [stealth.folder for stealth in stealth_sources]
    if duplicate := next((f for f in folders if folders.count(f) > 1), None):
        raise LoadoutError(f"{path}: the path {duplicate} is registered more than once")
    return stealth_sources


class UnreadableRegistry(LoadoutError):
    pass


def load_registry(path: Path) -> tuple[RegisteredStealth, ...]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ()
    except OSError as error:
        raise UnreadableRegistry(f"{path}: {error}") from None
    except UnicodeDecodeError as error:
        raise LoadoutError(f"{path}: {error}") from None
    return parse_registry(text, path)


def _render_entry(stealth: RegisteredStealth) -> str:
    lines = [
        f"[stealth.{stealth.name}]",
        f"path = {json.dumps(str(stealth.folder), ensure_ascii=False)}",
    ]
    return "\n".join([*lines, *(["removing = true"] if stealth.removing else [])]) + "\n"


def render_registry(stealth_sources: Iterable[RegisteredStealth]) -> str:
    entries = [_render_entry(stealth) for stealth in sorted(stealth_sources, key=lambda o: o.name)]
    return _HEADER + "\n".join(entries)


def with_stealth(
    stealth_sources: Sequence[RegisteredStealth], added: RegisteredStealth
) -> tuple[RegisteredStealth, ...]:
    if any(o.name == added.name for o in stealth_sources):
        raise LoadoutError(
            f"a stealth source named {added.name} is already registered; pass --name"
        )
    if (held := find_by_folder(stealth_sources, added.folder)) is not None:
        raise LoadoutError(
            f"{added.folder} is already registered as the stealth source {held.name}"
        )
    return tuple(sorted((*stealth_sources, added), key=lambda o: o.name))


def without_stealth(
    stealth_sources: Sequence[RegisteredStealth], name: str
) -> tuple[RegisteredStealth, ...]:
    return tuple(o for o in stealth_sources if o.name != name)


def marked_removing(
    stealth_sources: Sequence[RegisteredStealth], name: str
) -> tuple[RegisteredStealth, ...]:
    return tuple(replace(o, removing=True) if o.name == name else o for o in stealth_sources)


def find_by_folder(
    stealth_sources: Sequence[RegisteredStealth], folder: Path
) -> RegisteredStealth | None:
    return next((o for o in stealth_sources if same_path(o.folder, folder)), None)


def update_registry(
    change: Callable[[tuple[RegisteredStealth, ...]], tuple[RegisteredStealth, ...]],
) -> tuple[RegisteredStealth, ...]:
    path = stealth_path()
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
