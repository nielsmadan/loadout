"""Reading a file whole and replacing one atomically: the one way loadout replaces a file
(review 3, batch 4.4), kept apart from the output types (review 4, batch 3.5)."""

from __future__ import annotations

import contextlib
import os
import re
import signal
import stat
import tempfile
import threading
from collections.abc import Callable, Iterator
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


# The name of a write in progress, which a kill before its rename leaves behind: this
# prefix and `mkstemp`'s eight random characters, a shape no file of the user's has by
# chance (review 5, batch 1.1).
TEMPORARY_PREFIX = ".loadout-tmp-"
_TEMPORARY_NAME = re.compile(rf"{re.escape(TEMPORARY_PREFIX)}[a-z0-9_]{{8}}")


def temporary_name(name: str) -> bool:
    """Whether `name` is one this module gives a write in progress."""
    return _TEMPORARY_NAME.fullmatch(name) is not None


def replace_atomically(
    path: Path,
    fill: Callable[[BinaryIO], object],
    *,
    mode: int | None = None,
    durable: bool = False,
    before_rename: Callable[[], None] = lambda: None,
) -> None:
    """The one way a file is replaced (review 3, batch 4.4): `fill` writes a file beside `path`,
    which is renamed over it, so a kill never leaves `path` torn. The mode is set before
    the rename, so an executable never appears without its bit."""
    temporary: str | None = None
    try:
        # Ctrl-C waits until the name is held, so the cleanup below always knows the file.
        with _sigint_deferred():
            descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=TEMPORARY_PREFIX)
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
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
        raise


@contextlib.contextmanager
def _sigint_deferred() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


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
