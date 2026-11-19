from __future__ import annotations

import stat
from pathlib import Path

from .atomic import replace_atomically
from .claude_json import escape_surrogates
from .errors import LoadoutError
from .files import process_umask, text_mode


class ChangedDuringSync(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


def read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def write_if_unchanged(
    path: Path, text: str, expected: bytes | None, format_name: str = "json"
) -> None:
    try:
        data = escape_surrogates(text).encode("utf-8")
    except UnicodeEncodeError as error:
        raise LoadoutError(f"{path} cannot be written as UTF-8: {error.reason}") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = text_mode(_mode(path), data, format_name, process_umask())

    def unchanged_since_read() -> None:
        if read_bytes(path) != expected:
            raise ChangedDuringSync(path)

    replace_atomically(
        path, lambda handle: handle.write(data), mode=mode, before_rename=unchanged_since_read
    )
