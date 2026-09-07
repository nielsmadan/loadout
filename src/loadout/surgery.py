from __future__ import annotations

import json

from .errors import LoadoutError
from .toml_paths import apply_paths, sections


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
