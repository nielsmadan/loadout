"""The before-image of every path an adoption touches, kept so it can be put back (D-06).

`init` writes it before its first change and clears the in-progress marker once it is done.
A failure restores it straight away; a run killed part way leaves the marker, and the next
`init` offers the restore. The latest completed backup stays, so an adoption can be undone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .deployment import atomic_install
from .errors import LoadoutError
from .output import FrozenFile
from .output import no_symlinks as _no_symlinks

BACKUP_VERSION = 1
MANIFEST = "manifest.json"
MARKER = "in-progress"


@dataclass(frozen=True)
class Image:
    kind: str = "absent"
    content: bytes = field(default=b"", repr=False)
    mode: int = 0

    def digest(self) -> str:
        return hashlib.sha256(f"{self.kind}:{self.mode:o}:".encode() + self.content).hexdigest()


@dataclass(frozen=True)
class Operation:
    path: Path
    before: Image
    after: Image
    phase: str


def snapshot(path: Path) -> Image:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return Image()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return Image("symlink", os.fsencode(os.readlink(path)), mode)
    if stat.S_ISDIR(metadata.st_mode):
        return Image("directory", mode=mode)
    if stat.S_ISREG(metadata.st_mode):
        return Image("file", path.read_bytes(), mode)
    raise LoadoutError(f"unsupported migration entry: {path}")


def install(path: Path, image: Image) -> None:
    _no_symlinks(path.parent)
    current = snapshot(path)
    if current.kind == "directory" and image.kind != "directory":
        path.rmdir()
    elif current.kind == "symlink" or (current.kind == "file" and image.kind != "file"):
        path.unlink()
    if image.kind == "file":
        atomic_install(path, FrozenFile(image.content, image.mode))
    elif image.kind == "directory":
        path.mkdir(mode=image.mode, exist_ok=True)
        path.chmod(image.mode)
    elif image.kind == "symlink":
        path.symlink_to(os.fsdecode(image.content))


def backups_root(env: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    state = environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(environ.get("HOME", "~")).expanduser() / ".local/state"
    return (base / "loadout" / "backups").resolve()


@dataclass(frozen=True)
class Backup:
    directory: Path

    @property
    def interrupted(self) -> bool:
        return (self.directory / MARKER).exists()

    def manifest(self) -> dict[str, Any]:
        try:
            document = json.loads((self.directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LoadoutError(f"unreadable backup manifest: {self.directory}: {error}") from None
        if not isinstance(document, dict) or document.get("version") != BACKUP_VERSION:
            raise LoadoutError(f"unsupported backup manifest: {self.directory}")
        return document

    def images(self) -> dict[Path, Image]:
        return {
            Path(entry["path"]): Image(
                entry["kind"], base64.b64decode(entry["content"]), entry["mode"]
            )
            for entry in self.manifest()["entries"]
        }


def _covering(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Each path plus every ancestor a write could create: the missing ones up to the
    first that exists, which a restore must remove again."""
    covered: set[Path] = set()
    for path in paths:
        covered.add(path)
        for parent in path.parents:
            if parent.exists() or parent.is_symlink():
                break
            covered.add(parent)
    return tuple(sorted(covered))


def create_backup(paths: Iterable[Path], *, now: datetime, label: Mapping[str, object]) -> Backup:
    root = backups_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = root / now.strftime("%Y%m%dT%H%M%S%fZ")
    directory.mkdir(mode=0o700)
    (directory / MARKER).write_text("")
    covered = _covering(paths)
    captured = frozenset(covered)
    entries = []
    for path in covered:
        # Beneath a link init replaces, the path is the link target's entry, not ours.
        image = Image() if _beneath_link(path, captured) else snapshot(path)
        entries.append(
            {
                "path": str(path),
                "kind": image.kind,
                "mode": image.mode,
                "content": base64.b64encode(image.content).decode(),
            }
        )
    document = {"version": BACKUP_VERSION, **label, "entries": entries, "after": None}
    atomic_install(
        directory / MANIFEST, FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600)
    )
    return Backup(directory)


def complete(backup: Backup) -> None:
    """Record what the adoption left at each path, clear the marker and keep only this
    backup, so a later restore can tell an untouched path from one edited since."""
    document = backup.manifest()
    document["after"] = {
        entry["path"]: snapshot(Path(entry["path"])).digest() for entry in document["entries"]
    }
    atomic_install(
        backup.directory / MANIFEST,
        FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600),
    )
    (backup.directory / MARKER).unlink(missing_ok=True)
    for other in backups():
        if other.directory != backup.directory and not other.interrupted:
            shutil.rmtree(other.directory)


def backups() -> tuple[Backup, ...]:
    root = backups_root()
    if not root.is_dir():
        return ()
    return tuple(Backup(directory) for directory in sorted(root.iterdir()) if directory.is_dir())


def interrupted_backup() -> Backup | None:
    return next((b for b in reversed(backups()) if b.interrupted), None)


def latest_backup() -> Backup | None:
    found = backups()
    return found[-1] if found else None


def _remove(path: Path, *, whole: bool) -> bool:
    """Remove what init created at `path`; a directory goes whole only when `whole`, and
    otherwise only once empty, so anything added inside it since survives."""
    if path.is_dir() and not path.is_symlink():
        if whole:
            shutil.rmtree(path)
            return True
        try:
            path.rmdir()
        except OSError:
            return False
        return True
    if path.exists() or path.is_symlink():
        path.unlink()
    return True


def _beneath_link(path: Path, captured: frozenset[Path]) -> bool:
    """Whether a captured ancestor is a symlink now, so `path` is an entry of its target."""
    return any(parent in captured and parent.is_symlink() for parent in path.parents)


def restore(backup: Backup, *, disposable: frozenset[Path] = frozenset()) -> tuple[Path, ...]:
    """Put every path back as the backup found it, and return the paths it left alone.

    A completed backup recorded what the adoption left behind; a path that has changed
    since is someone else's edit and stays, reported. An interrupted one has no record,
    so every path is restored. A `disposable` directory goes whole even then: the caller
    has checked it holds nothing of the user's.
    """
    images = backup.images()
    after = backup.manifest().get("after")
    whole = after is None
    kept = [
        path
        for path in images
        if after is not None and snapshot_digest(path) != after.get(str(path))
    ]
    restored = {path: image for path, image in images.items() if path not in kept}
    captured = frozenset(images)
    created = (
        path
        for path, image in restored.items()
        if image.kind == "absent" and not _beneath_link(path, captured)
    )
    for path in sorted(created, key=lambda p: len(p.parts), reverse=True):
        if not _remove(path, whole=whole or path in disposable):
            kept.append(path)
    for path in sorted(restored, key=lambda p: len(p.parts)):
        image = restored[path]
        if image.kind == "absent" or path in kept or snapshot(path) == image:
            continue
        replaced = image.kind != "directory" or snapshot(path).kind != "directory"
        if replaced and not _remove(path, whole=whole):
            kept.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        install(path, image)
    (backup.directory / MARKER).unlink(missing_ok=True)
    return tuple(sorted(set(kept)))


def snapshot_digest(path: Path) -> str:
    try:
        return snapshot(path).digest()
    except LoadoutError:
        return ""
