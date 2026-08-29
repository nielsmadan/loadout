from __future__ import annotations

from pathlib import Path

BANNER = "# Keys loadout manages in this destination. Generated; edit the fragment instead."


def read_record(path: Path) -> frozenset[str]:
    """Key names loadout wrote here last time, or nothing if it never has.

    A missing record reads as empty rather than raising: the first sync on a
    machine has nothing to reconcile, and so does a fragment that never had one.
    """
    if not path.is_file():
        return frozenset()
    return frozenset(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def render_record(keys: frozenset[str]) -> str:
    """One key per line, sorted.

    Two machines syncing both rewrite this file, so it is line-oriented and
    ordered to keep the inevitable merge conflict trivial to resolve.
    """
    if not keys:
        return f"{BANNER}\n"
    return "\n".join([BANNER, *sorted(keys)]) + "\n"


def owned_now(recorded: frozenset[str], present: frozenset[str]) -> frozenset[str]:
    """What to strip: everything written last time, plus everything written now.

    The union is the whole mechanism. A key dropped from the fragment is absent
    from `present` but still in `recorded`, so it is stripped rather than left
    behind — the case a set derived from the fragment alone cannot express
    (ADR 0017).
    """
    return recorded | present
