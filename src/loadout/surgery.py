from __future__ import annotations

import json
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


def _opens_multiline(line: str) -> bool:
    """Whether this assignment starts a triple-quoted value it does not close.

    An opener that also closes on the same line is one line and merges fine; an
    unterminated one owns the lines below it, which a line-wise merge cannot see.
    """
    _, _, value = line.partition("=")
    return value.count(chr(34) * 3) % 2 == 1 or value.count(chr(39) * 3) % 2 == 1


def _closes_multiline(line: str) -> bool:
    """Whether this continuation line ends the triple-quoted value it belongs to."""
    return (chr(34) * 3) in line or (chr(39) * 3) in line


def _merge_preamble(
    have_preamble: list[str], scalars: dict[str, str], owned: frozenset[str]
) -> tuple[list[str], set[str]]:
    """The existing preamble with owned keys replaced in place or removed.

    Split out of `apply_toml` so the multi-line cases read as the two different
    operations they are: replacing one is refused because it would orphan the value's
    body, while removing one only has to skip to the closing delimiter.
    """
    kept: list[str] = []
    placed: set[str] = set()
    # Set while skipping the body of a multi-line owned key being removed; its
    # continuation lines are not assignments, so nothing else would drop them.
    dropping = False
    for line in have_preamble:
        if dropping:
            dropping = not _closes_multiline(line)
            continue
        key = _assigned_key(line)
        if key is not None and key in owned and _opens_multiline(line):
            if key in scalars:
                # Replacing it would leave the rest of the value orphaned at top level
                # and the document would stop parsing. A newline *inside* the value is
                # fine — the renderer escapes it onto one line and that form merges
                # cleanly. It is the destination's shape that cannot be replaced.
                raise LoadoutError(
                    f"{key!r} is written across several lines in the destination, which a "
                    f"line-wise merge cannot replace without orphaning the rest of it. "
                    f"Rewrite it on one line, or leave the key to whatever owns the file."
                )
            # Removing one needs no such care. Declaring a key owned and rendering no
            # value for it is how loadout evicts a key another tool keeps writing.
            dropping = True
            continue
        if key is None or key not in owned:
            kept.append(line)
        elif key in scalars:
            kept.append(scalars[key])
            placed.add(key)
    return kept, placed


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
    kept_preamble, placed = _merge_preamble(have_preamble, scalars, owned)
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


def apply_json(existing: str, owned: frozenset[str], document: str) -> str:
    """The JSON counterpart of `apply_toml`, and it may parse where that one must not.

    `apply_toml` works line-wise because a round trip loses comments, another tool's
    managed block and multi-line strings. **JSON has none of those**, and Python
    preserves key order, so parsing loses nothing. Verified against Claude Code
    2.1.251's ~465KB `.claude.json`: `json.dumps(indent=2, ensure_ascii=False)`
    reproduces it byte for byte, bar a trailing newline.

    Owned keys are assigned **in place** for the same reason `apply_toml` replaces
    where content already sits: popping and re-adding would move the key to the end
    every time the harness rewrote the file, and `check` would report that reshuffle
    as drift on every run.

    An owned key the document no longer carries is removed — that is what makes
    dropping a server drop it.
    """
    base: object = json.loads(existing) if existing.strip() else {}
    if not isinstance(base, dict):
        raise LoadoutError("cannot merge into a JSON document that is not an object")
    wanted = json.loads(document)
    if not isinstance(wanted, dict):
        raise LoadoutError("a merged JSON fragment must be an object")
    for key, value in wanted.items():
        base[key] = value
    for key in owned - set(wanted):
        base.pop(key, None)
    rendered = json.dumps(base, indent=2, ensure_ascii=False)
    # Match the file's own convention rather than imposing one: Claude writes this
    # file without a trailing newline, and adding one would read as drift forever.
    return rendered + "\n" if existing.endswith("\n") else rendered
