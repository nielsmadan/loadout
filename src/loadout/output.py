from __future__ import annotations

import hashlib
import stat
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import tomlkit
from tomlkit.items import Item

from .errors import LoadoutError

Scope = Literal["project", "global"]
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

    def read_bytes(self) -> bytes:
        return self.source.read_bytes()

    def file_mode(self) -> int:
        return stat.S_IMODE(self.source.stat().st_mode)


@dataclass(frozen=True)
class Merged:
    owned: frozenset[str]
    document: str
    records: tuple[tuple[Path, str], ...] = ()
    format: Literal["toml", "json"] = "toml"
    # Owned only to be stripped (`$remove`): the harness writing one back is no edit.
    evicted: frozenset[str] = frozenset()

    @property
    def guarded(self) -> frozenset[str]:
        """The owned keys loadout writes a value for, and so the only ones an edit can
        touch: an evicted one is stripped whatever it holds."""
        return self.owned - self.evicted


@dataclass(frozen=True)
class FrozenFile:
    content: bytes
    mode: int


class SharedText(str):
    """A whole project document whose keys loadout does not own came from the file on disk,
    so one that no longer parses is replaced only under `sync --force`."""

    __slots__ = ()


Output = str | Copied | Merged | FrozenFile


def output_record_paths(outputs: Mapping[Path, Output]) -> tuple[Path, ...]:
    """The ownership records the merged outputs write beside themselves."""
    return tuple(
        record
        for content in outputs.values()
        if isinstance(content, Merged)
        for record, _ in content.records
    )


def entry_path(path: Path) -> Path:
    """The one spelling of an entry: its parent resolved, the entry itself not followed."""
    return path.parent.resolve() / path.name


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    """The digest of a file's bytes, read a chunk at a time. A caller that hashes one
    source for several harnesses keeps its own memo (`digests`)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def digests() -> Callable[[Path], str]:
    """`file_sha256` remembered for one pass, so a source copied to several harnesses is
    read once; a pass is short enough that the file cannot change under it unnoticed."""
    known: dict[Path, str] = {}

    def digest(path: Path) -> str:
        if path not in known:
            known[path] = file_sha256(path)
        return known[path]

    return digest


def item_name(name: str, path: Path) -> str:
    """A skill or agent name becomes a path segment and a `.gitignore` line, so it may
    hold no control character and no separator, and may not be `.` or `..`."""
    if any(unicodedata.category(c) == "Cc" for c in name):
        raise LoadoutError(f"{path!r}: a name may not contain a control character")
    # Python splits lines on these too, so one would break the name across two lines.
    if any(unicodedata.category(c) in {"Zl", "Zp"} for c in name):
        raise LoadoutError(f"{path!r}: a name may not contain a line or paragraph separator")
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
