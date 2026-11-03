from __future__ import annotations

import errno
from pathlib import Path


class LoadoutError(Exception):
    """Any error loadout raises deliberately."""


class UsageError(LoadoutError):
    pass


class Interrupted(LoadoutError):
    """Ctrl-C, reported with what it left: the message says what was and was not changed."""


WRITE_DENIED = frozenset({errno.EACCES, errno.EPERM, errno.EROFS})


def write_denied(error: OSError) -> bool:
    return error.errno in WRITE_DENIED


def unwritable(path: Path, error: OSError) -> LoadoutError:
    """`path` could not be written because a folder on its way refused it; the error's own
    filename is that folder's entry, a temporary file or a directory being created."""
    folder = Path(error.filename).parent if error.filename else path.parent
    return LoadoutError(f"cannot write {path}: {folder} is not writable ({error.strerror})")
