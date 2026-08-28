from __future__ import annotations

import re

from .errors import LoadoutError

TABLE_HEADER = re.compile(r"^\s*\[\[?([^\]]+)\]\]?\s*$")
ASSIGNMENT = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=")


def _table_root(line: str) -> str | None:
    """The first segment of a table header, or None if the line is not one.

    Split on the first dot outside quotes: `[projects."/Users/me"]` is rooted at
    `projects`, and the path carries a dot that is part of a key, not a separator.
    """
    match = TABLE_HEADER.match(line)
    if match is None:
        return None
    path = match.group(1).strip()
    depth = 0
    for index, char in enumerate(path):
        if char == '"':
            depth ^= 1
        elif char == "." and not depth:
            return path[:index].strip()
    return path


def _assigned_key(line: str) -> str | None:
    match = ASSIGNMENT.match(line)
    return match.group(1) if match else None


def strip_owned(existing: str, owned: frozenset[str]) -> str:
    """Remove every top-level scalar and every table rooted at an owned name.

    `owned` is **declared**, never derived from what is being written. Deriving it
    from the current document cannot express a removal: a server dropped from the
    source is absent from the derived set, so nothing strips it and it survives
    every later run — stale, and indistinguishable from content a person added.
    That is a live defect in the prototype this ports (ADR 0017).
    """
    kept: list[str] = []
    skipping = False
    for line in existing.splitlines():
        root = _table_root(line)
        if root is not None:
            skipping = root in owned
        elif not skipping and (key := _assigned_key(line)) is not None and key in owned:
            continue
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def _split_document(document: str) -> tuple[list[str], list[str]]:
    lines = document.splitlines()
    for index, line in enumerate(lines):
        if _table_root(line) is not None:
            return lines[:index], lines[index:]
    return lines, []


def apply_toml(existing: str, owned: frozenset[str], document: str) -> str:
    """Write `document`'s keys into `existing`, replacing what loadout owns.

    Never parses and reserialises: comments, a managed block another tool owns and
    a multi-line basic string are not in the parsed model, so a round trip destroys
    exactly the content this exists to preserve. The file is safe because nothing
    rewrites those bytes, not because a writer was careful.
    """
    scalars, tables = _split_document(document)
    body = strip_owned(existing, owned)

    # A bare key after a table header reads as a member of that table, so an owned
    # scalar has to land above the first one rather than at the end.
    lines = body.splitlines()
    cut = next((i for i, line in enumerate(lines) if _table_root(line) is not None), len(lines))
    scalar_block = [line for line in scalars if line.strip()]
    head, tail = lines[:cut], lines[cut:]
    while head and not head[-1].strip():
        head.pop()

    parts: list[str] = []
    if head or scalar_block:
        parts.append("\n".join([*head, *scalar_block]))
    if tail:
        parts.append("\n".join(tail))
    if tables:
        parts.append("\n".join(tables).strip("\n"))
    merged = "\n\n".join(part for part in parts if part.strip())
    return merged + "\n" if merged else ""


def reject_nested(values: dict[str, object], label: str) -> None:
    """A managed value is a top-level scalar or array; a table is refused by name.

    Silently flattening one would move a key into whichever table precedes it,
    changing what it configures rather than failing.
    """
    for key, value in values.items():
        if isinstance(value, dict):
            raise LoadoutError(f"{label}: {key!r} is a table; only top-level keys may be managed")
