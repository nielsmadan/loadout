from __future__ import annotations

from pathlib import Path

from .errors import LoadoutError
from .toml_paths import key_name, key_path

BANNER = "# Keys loadout manages in this destination. Generated; edit the fragment instead."
FORMAT_PREFIX = "# loadout-owned-format:"
FORMAT = f"{FORMAT_PREFIX} 2"


def read_record(path: Path) -> frozenset[str]:
    """Key names loadout wrote here last time, or nothing if it never has.

    A missing record reads as empty rather than raising: the first sync on a
    machine has nothing to reconcile, and so does a fragment that never had one.
    """
    if not path.is_file():
        return frozenset()
    lines = path.read_text(encoding="utf-8").split("\n")
    formats = [line.strip() for line in lines if line.strip().startswith(FORMAT_PREFIX)]
    if formats and formats != [FORMAT]:
        raise LoadoutError(f"{path}: unsupported ownership record format")
    names = (line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
    return frozenset(key_name(key_path(name) if formats else (name,)) for name in names)


def render_record(keys: frozenset[str]) -> str:
    """One key per line, sorted.

    Two machines syncing both rewrite this file, so it is line-oriented and
    ordered to keep the inevitable merge conflict trivial to resolve.
    """
    return "\n".join([BANNER, FORMAT, *sorted(keys)]) + "\n"


def owned_now(recorded: frozenset[str], present: frozenset[str]) -> frozenset[str]:
    """What to strip: everything written last time, plus everything written now.

    The union is the whole mechanism. A key dropped from the fragment is absent
    from `present` but still in `recorded`, so it is stripped rather than left
    behind — the case a set derived from the fragment alone cannot express
    (ADR 0017).
    """
    return recorded | present
