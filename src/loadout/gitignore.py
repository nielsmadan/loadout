from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .errors import LoadoutError

SPECIAL = "\\*?[] "


def escape(line: str) -> str:
    """A root-anchored path as the `.gitignore` line that matches it literally."""
    if any(character in line for character in "\n\r\0"):
        raise LoadoutError(f"a path with a line break cannot be listed in .gitignore: {line!r}")
    return "".join("\\" + c if c in SPECIAL else c for c in line)


def gitignore_path(root: Path) -> Path:
    """The project's `.gitignore`, refused when it is not a plain file of the project:
    a link would have the lines appended to whatever it names."""
    path = root / ".gitignore"
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LoadoutError(f"{path} must be a regular file, not a symlink, for loadout to edit it")
    return path


def with_lines(text: str, lines: Iterable[str]) -> str:
    """`text` with each line it lacks appended, everything already there untouched."""
    lines = tuple(lines)
    if any("\n" in line or "\r" in line for line in lines):
        raise LoadoutError("a .gitignore entry may not contain a line break")
    present = set(text.splitlines())
    missing = [line for line in dict.fromkeys(lines) if line not in present]
    if not missing:
        return text
    return text + ("\n" if text and not text.endswith("\n") else "") + "\n".join(missing) + "\n"


def append_lines(root: Path, lines: Iterable[str]) -> int:
    """Append the lines not already there; returns how many that was."""
    path = gitignore_path(root)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    updated = with_lines(text, lines)
    if updated == text:
        return 0
    path.write_text(updated, encoding="utf-8")
    return len(updated.splitlines()) - len(text.splitlines())


def remove_lines(root: Path, lines: Iterable[str]) -> int:
    """Drop exactly these lines, keeping every other line where it is."""
    path = gitignore_path(root)
    if not path.is_file():
        return 0
    unwanted = set(lines)
    existing = path.read_text(encoding="utf-8").splitlines()
    kept = [line for line in existing if line not in unwanted]
    if len(kept) == len(existing):
        return 0
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    return len(existing) - len(kept)
