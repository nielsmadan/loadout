"""What a path holds, captured and put back: the unit init plans, backs up and restores."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .errors import LoadoutError
from .output import FrozenFile, atomic_install, sha256
from .output import no_symlinks as _no_symlinks

ImageKind = Literal["absent", "file", "directory", "symlink"]
Phase = Literal["topology", "source", "retire", "registration", "ignore"]


@dataclass(frozen=True)
class Image:
    kind: ImageKind = "absent"
    content: bytes = field(default=b"", repr=False)
    mode: int = 0

    def digest(self) -> str:
        return sha256(f"{self.kind}:{self.mode:o}:".encode() + self.content)

    def shape(self) -> str:
        """What a planned write fixes ahead of time: the kind and the content, not the
        mode, which a co-owned file keeps from whatever is already there."""
        return f"{self.kind}:{sha256(self.content)}"


@dataclass(frozen=True)
class Operation:
    path: Path
    before: Image
    after: Image
    phase: Phase


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


def install(path: Path, image: Image, boundary: Path | None = None) -> None:
    """Make `path` hold `image`. A file replaces a file atomically, never by unlinking it
    first; links are refused between `path` and `boundary` (or `/`)."""
    _no_symlinks(path.parent, boundary)
    current = snapshot(path)
    if current.kind == "directory" and image.kind != "directory":
        path.rmdir()
    elif current.kind == "symlink" or (current.kind == "file" and image.kind != "file"):
        path.unlink()
    if image.kind == "file":
        atomic_install(path, FrozenFile(image.content, image.mode), boundary)
    elif image.kind == "directory":
        path.mkdir(mode=image.mode, exist_ok=True)
        path.chmod(image.mode)
    elif image.kind == "symlink":
        path.symlink_to(os.fsdecode(image.content))
