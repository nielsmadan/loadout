from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TextIO

RED = "31"
GREEN = "32"
CYAN = "36"
BOLD = "1"
BOLD_YELLOW = "1;33"

_FORCE_OFF = frozenset({"", "0", "false"})


def color_enabled(stream: TextIO, environ: Mapping[str, str]) -> bool:
    if environ.get("NO_COLOR"):
        return False
    if environ.get("FORCE_COLOR", "").lower() not in _FORCE_OFF:
        return True
    if environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty and isatty())
    except (OSError, ValueError):
        return False


def paint(text: str, style: str) -> str:
    body = text.removesuffix("\n")
    return f"\x1b[{style}m{body}\x1b[0m{text[len(body) :]}"


def print_styled(
    text: str, style: str, stream: TextIO, environ: Mapping[str, str] | None = None
) -> None:
    on = color_enabled(stream, os.environ if environ is None else environ)
    print(paint(text, style) if on else text, file=stream)
