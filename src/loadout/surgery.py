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


def _blocks(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Split into the preamble and each table block, keeping every line verbatim.

    A block runs from its header to the line before the next one, trailing blanks
    included, so foreign spacing survives a rewrite untouched.
    """
    preamble: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        match = TABLE_HEADER.match(line)
        if match is not None:
            current = [line]
            blocks.append((match.group(1).strip(), current))
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    return preamble, blocks


def apply_toml(existing: str, owned: frozenset[str], document: str) -> str:
    """Write `document`'s keys into `existing`, replacing what loadout owns.

    Never parses and reserialises: comments, a managed block another tool owns and
    a multi-line basic string are not in the parsed model, so a round trip destroys
    exactly the content this exists to preserve. The file is safe because nothing
    rewrites those bytes, not because a writer was careful.

    Owned content is replaced **where it already sits** and appended only when new.
    Moving it instead would reorder the file whenever the harness appends a table of
    its own, and `check` would report that reshuffle as drift on every run — the
    spurious `modified outside loadout` this guard exists to avoid.
    """
    want_preamble, want_blocks = _blocks(document)
    have_preamble, have_blocks = _blocks(existing)
    wanted = dict(want_blocks)

    scalars = {key: line for line in want_preamble if (key := _assigned_key(line)) is not None}
    kept_preamble: list[str] = []
    placed: set[str] = set()
    for line in have_preamble:
        key = _assigned_key(line)
        if key is None or key not in owned:
            kept_preamble.append(line)
        elif key in scalars:
            kept_preamble.append(scalars[key])
            placed.add(key)
    while kept_preamble and not kept_preamble[-1].strip():
        kept_preamble.pop()
    for key, line in scalars.items():
        if key not in placed:
            kept_preamble.append(line)

    kept_blocks: list[list[str]] = []
    seen: set[str] = set()
    for path, lines in have_blocks:
        root = _table_root(lines[0])
        if root is not None and root in owned:
            if path in wanted:
                kept_blocks.append(list(wanted[path]))
                seen.add(path)
            continue
        kept_blocks.append(lines)
    for path, lines in want_blocks:
        if path not in seen:
            kept_blocks.append(list(lines))

    parts = ["\n".join(kept_preamble).strip("\n")] if kept_preamble else []
    parts += ["\n".join(block).strip("\n") for block in kept_blocks]
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


def concat_documents(fragments: tuple[str, ...]) -> str:
    """Join TOML fragments so every top-level key stays above every table.

    Concatenating naively puts a later fragment's scalars after an earlier one's
    table header, where TOML reads them as members of that table — the key would
    still be spelled right and would configure something else entirely.
    """
    preambles: list[str] = []
    blocks: list[str] = []
    for fragment in fragments:
        preamble, tables = _blocks(fragment)
        preambles.extend(line for line in preamble if line.strip())
        blocks.extend("\n".join(lines).strip("\n") for _path, lines in tables)
    parts = ["\n".join(preambles)] if preambles else []
    parts += blocks
    return "\n\n".join(part for part in parts if part.strip())
