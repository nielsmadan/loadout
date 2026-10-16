from __future__ import annotations

import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import tomlkit
from tomlkit.items import Item

from .errors import LoadoutError

ArtifactScope = Literal["project", "global"]
CATEGORIES = frozenset(
    {
        "instructions",
        "permissions",
        "mcp-permissions",
        "mcp",
        "settings",
        "hooks",
        "plugins",
        "skills",
        "module-config",
        "support",
        "templates",
    }
)


@dataclass(frozen=True)
class Copied:
    source: Path
    prefix: bytes = b""
    mode: int | None = None

    def read_bytes(self) -> bytes:
        return self.prefix + self.source.read_bytes()

    def file_mode(self) -> int:
        return self.mode if self.mode is not None else stat.S_IMODE(self.source.stat().st_mode)


@dataclass(frozen=True)
class Merged:
    owned: frozenset[str]
    document: str
    records: tuple[tuple[Path, str], ...] = ()
    format: str = "toml"
    native: bool = False
    emit_empty: bool = False


@dataclass(frozen=True)
class FrozenFile:
    content: bytes
    mode: int


Output = str | Copied | Merged | FrozenFile


def relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise LoadoutError(f"{label} must be a string")
    path = PurePosixPath(value)
    if path.is_absolute() or not value or ".." in path.parts or path == PurePosixPath("."):
        raise LoadoutError(f"{label} must be a relative path inside its root: {value!r}")
    return path


def no_symlinks(path: Path, root: Path | None = None) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise LoadoutError(f"path is a symlink: {current}")
        if current in (root, current.parent):
            break
        current = current.parent


def item_name(name: str, path: Path) -> str:
    """A skill or agent name becomes a path segment and a `.gitignore` line, so it may
    hold no control character and no separator, and may not be `.` or `..`."""
    if any(unicodedata.category(c) == "Cc" for c in name):
        raise LoadoutError(f"{path!r}: a name may not contain a control character")
    if "/" in name or name in {"", ".", ".."}:
        raise LoadoutError(f"{path}: {name!r} is not a usable name")
    return name


def regular_file(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise LoadoutError(f"source must be a regular file: {path}")


def json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant {value}")


def toml_item(value: Any) -> Item:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        for key, item in value.items():
            table.add(key, toml_item(item))
        return table
    if isinstance(value, list):
        array = tomlkit.array()
        for item in value:
            array.append(toml_item(item))
        return array
    result: Item = tomlkit.item(value)
    return result
