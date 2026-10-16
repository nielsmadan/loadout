from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Literal

import tomlkit
from tomlkit.items import Item

from .containment import no_symlinks
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


@dataclass(frozen=True)
class FrozenFile:
    content: bytes
    mode: int


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


def read_file(path: Path) -> FrozenFile | None:
    no_symlinks(path)
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise LoadoutError(f"parent must be a directory: {parent}")
    if not path.exists():
        return None
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise LoadoutError(f"path must be a regular file: {path}")
    return FrozenFile(path.read_bytes(), stat.S_IMODE(metadata.st_mode))


# The name of a write in progress, which a kill before its rename leaves behind.
TEMPORARY_PREFIX = ".loadout-"


def replace_atomically(
    path: Path,
    fill: Callable[[BinaryIO], object],
    *,
    mode: int | None = None,
    durable: bool = False,
    before_rename: Callable[[], None] = lambda: None,
) -> None:
    """The one atomic writer (review 3, batch 4.4): `fill` writes a file beside `path`,
    which is renamed over it, so a kill never leaves `path` torn. The mode is set before
    the rename, so an executable never appears without its bit."""
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=TEMPORARY_PREFIX)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            fill(handle)
            if mode is not None:
                os.fchmod(handle.fileno(), mode)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        before_rename()
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def atomic_install(path: Path, frozen: FrozenFile, boundary: Path | None = None) -> None:
    """Replace `path` with `frozen`, durably, refusing a link between it and `boundary`
    (or `/`) before the write and again before the rename."""
    no_symlinks(path, boundary)
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_atomically(
        path,
        lambda handle: handle.write(frozen.content),
        mode=frozen.mode,
        durable=True,
        before_rename=lambda: no_symlinks(path, boundary),
    )


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
