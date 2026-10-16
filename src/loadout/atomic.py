"""Reading a file whole and replacing one atomically: the one way loadout writes a file
(review 3, batch 4.4), kept apart from the output types (review 4, batch 3.5)."""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from .containment import no_symlinks
from .errors import LoadoutError
from .output import FrozenFile


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
