from __future__ import annotations

from pathlib import Path

from .errors import LoadoutError


def read_source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise LoadoutError(f"{path}: not UTF-8: {error.reason}; re-encode it as UTF-8") from error
