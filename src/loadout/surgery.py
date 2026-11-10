from __future__ import annotations

import json
from typing import Any, Literal

from .errors import LoadoutError
from .toml_paths import KeyPath, apply_paths, key_path, sections


def apply_toml(existing: str, owned: frozenset[str], document: str) -> str:
    return apply_paths(existing, owned, document)


def concat_documents(fragments: tuple[str, ...]) -> str:
    preambles: list[str] = []
    blocks: list[str] = []
    for fragment in fragments:
        preamble, *tables = sections(fragment)
        preambles.extend(
            entry.text.rstrip("\r\n") for entry in preamble.entries if entry.text.strip()
        )
        blocks.extend(table.text().strip("\r\n") for table in tables)
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

    An owned name is a dotted path, so a leaf inside a table the harness also writes
    to is set or removed alone, and the table's other keys stay.
    """
    base: object = json.loads(existing) if existing.strip() else {}
    if not isinstance(base, dict):
        raise LoadoutError("cannot merge into a JSON document that is not an object")
    incoming = json.loads(document)
    if not isinstance(incoming, dict):
        raise LoadoutError("a merged JSON fragment must be an object")
    order = {path: index for index, path in enumerate(_paths(incoming))}
    paths = sorted((key_path(name) for name in owned), key=lambda p: (order.get(p, len(order)), p))
    for path in paths:
        value = _at(incoming, path)
        if value is _ABSENT:
            _remove(base, path)
        else:
            _place(base, path, value)
    rendered = json.dumps(base, indent=2, ensure_ascii=False)
    # Match the file's own convention rather than imposing one: Claude writes this
    # file without a trailing newline, and adding one would read as drift forever.
    return rendered + "\n" if existing.endswith("\n") else rendered


_ABSENT = object()


def _paths(document: dict[str, Any], prefix: KeyPath = ()) -> list[KeyPath]:
    """Every key path in the document, tables before what they hold, in its own order."""
    found: list[KeyPath] = []
    for key, value in document.items():
        path = (*prefix, key)
        found.append(path)
        if isinstance(value, dict):
            found.extend(_paths(value, path))
    return found


def _at(document: dict[str, Any], path: KeyPath) -> Any:
    value: Any = document
    for part in path:
        if not isinstance(value, dict) or part not in value:
            return _ABSENT
        value = value[part]
    return value


def _place(document: dict[str, Any], path: KeyPath, value: Any) -> None:
    table = document
    for part in path[:-1]:
        child = table.get(part)
        if not isinstance(child, dict):
            child = table[part] = {}
        table = child
    table[path[-1]] = value


def _remove(document: dict[str, Any], path: KeyPath) -> None:
    """Drop the leaf, then every table the drop left empty."""
    tables: list[dict[str, Any]] = [document]
    for part in path[:-1]:
        child = tables[-1].get(part)
        if not isinstance(child, dict):
            return
        tables.append(child)
    tables[-1].pop(path[-1], None)
    for depth in range(len(tables) - 1, 0, -1):
        if tables[depth]:
            return
        tables[depth - 1].pop(path[depth - 1])


# What a shared document holds of loadout's when loadout writes nothing into it.
DocumentFormat = Literal["json", "toml"]
EMPTY: dict[DocumentFormat, str] = {"json": "{}", "toml": ""}


def apply(
    existing: str, owned: frozenset[str], document: str, *, format_name: DocumentFormat
) -> str:
    """`existing` with loadout's `owned` keys set as `document` has them, in its format."""
    return (apply_json if format_name == "json" else apply_toml)(existing, owned, document)
