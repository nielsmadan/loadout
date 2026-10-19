"""The before-image of every path an adoption touches, kept for one run (ADR 0033).

`init` writes it before its first change and deletes it once the run ends: when the run
succeeds, or once a failure has put every path back. A run killed part way leaves it
marked in progress, and the next `init` puts it back before anything else.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic import atomic_install, temporary_name
from .errors import LoadoutError
from .machine import state_dir
from .migration_images import Image, install, snapshot
from .output import FrozenFile, sha256

BACKUP_VERSION = 5
MANIFEST = "manifest.json"
MARKER = "in-progress"
IMAGES = "images"
EMPTY = sha256(b"")


def backups_root(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "backups"


@dataclass(frozen=True)
class Backup:
    directory: Path

    def manifest(self) -> dict[str, Any]:
        try:
            document = json.loads((self.directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LoadoutError(
                f"unreadable backup manifest: {self.directory}: {error}; it cannot be put back, "
                "so delete that directory"
            ) from None
        if not isinstance(document, dict) or document.get("version") != BACKUP_VERSION:
            raise LoadoutError(
                f"backup {self.directory} was written by another version of loadout and "
                "cannot be restored; delete that directory"
            )
        return document

    def images(self) -> dict[Path, Image]:
        return {
            Path(entry["path"]): Image(entry["kind"], self._content(entry["sha256"]), entry["mode"])
            for entry in self.manifest()["entries"]
        }

    def _content(self, digest: str) -> bytes:
        if digest == EMPTY:
            return b""
        path = self.directory / IMAGES / digest
        try:
            content = path.read_bytes()
        except OSError:
            content = None
        if content is None or sha256(content) != digest:
            raise LoadoutError(f"backup image {path} is damaged or missing; nothing was restored")
        return content


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


def create_backup(paths: Iterable[Path], *, now: datetime, root: Path) -> Backup:
    """Record each path's before-image. Beneath a link init replaces, a path is an entry
    of the link's target, which putting the link back restores, so it is left out."""
    covered = _covering(paths)
    captured = frozenset(covered)
    images = {path: _readable(path) for path in covered if not _beneath_link(path, captured)}
    digests = {path: sha256(image.content) for path, image in images.items()}
    document = {
        "version": BACKUP_VERSION,
        "root": str(root),
        "entries": [
            {"path": str(path), "kind": image.kind, "mode": image.mode, "sha256": digests[path]}
            for path, image in images.items()
        ],
    }
    backups = backups_root()
    backups.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Backup(backups / now.strftime("%Y%m%dT%H%M%S%fZ"))
    try:
        backup.directory.mkdir(mode=0o700)
        (backup.directory / IMAGES).mkdir(mode=0o700)
        for path, image in images.items():
            if image.content:
                atomic_install(
                    backup.directory / IMAGES / digests[path], FrozenFile(image.content, 0o600)
                )
        atomic_install(
            backup.directory / MANIFEST,
            FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600),
        )
        # Last, so a marker always has a complete manifest beside it.
        (backup.directory / MARKER).write_text("")
    except BaseException:
        shutil.rmtree(backup.directory, ignore_errors=True)
        raise
    return backup


def _readable(path: Path) -> Image:
    try:
        return snapshot(path)
    except OSError as error:
        raise LoadoutError(f"cannot back up {path} before changing it: {error}") from None


def _beneath_link(path: Path, captured: frozenset[Path]) -> bool:
    return any(parent in captured and parent.is_symlink() for parent in path.parents)


def interrupted_backup() -> Backup | None:
    """The latest backup a killed run left marked in progress."""
    root = backups_root()
    if not root.is_dir():
        return None
    marked = (d for d in sorted(root.iterdir(), reverse=True) if (d / MARKER).is_file())
    return next((Backup(directory) for directory in marked), None)


def discard(backup: Backup) -> None:
    shutil.rmtree(backup.directory)


def restore(backup: Backup) -> tuple[Path, ...]:
    """Put every path back as its before-image, then delete the backup; the paths put
    back. What init created goes, deepest first, a folder with all it holds; then what
    it replaced comes back, shallowest first. A failure part way leaves the backup
    marked, so the next `init` finishes it."""
    images = backup.images()
    changed: list[Path] = []
    for path in sorted(images, key=lambda p: len(p.parts), reverse=True):
        image, current = images[path], snapshot(path)
        if current == image:
            continue
        changed.append(path)
        if current.kind == "directory" and image.kind != "directory":
            shutil.rmtree(path)
        elif image.kind == "absent":
            path.unlink()
    for path in sorted(images, key=lambda p: len(p.parts)):
        image = images[path]
        if image.kind != "absent" and snapshot(path) != image:
            install(path, image, _boundary(path, images))
            changed.append(path)
    for folder in sorted({path.parent for path in images}):
        _drop_interrupted_writes(folder)
    discard(backup)
    return tuple(sorted(set(changed)))


def _boundary(path: Path, images: Mapping[Path, Image]) -> Path | None:
    """The nearest captured ancestor: links above it are the user's layout, not init's."""
    return next((parent for parent in path.parents if parent in images), None)


def _drop_interrupted_writes(folder: Path) -> None:
    """Remove each write of loadout's a kill cut short in `folder`: a regular file named
    exactly as `atomic` names a write in progress. Any other file is someone else's."""
    with contextlib.suppress(OSError):
        for entry in folder.iterdir():
            if temporary_name(entry.name) and entry.is_file() and not entry.is_symlink():
                entry.unlink()
