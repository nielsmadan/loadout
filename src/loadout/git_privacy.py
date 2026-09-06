from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .errors import LoadoutError


def ignored_paths(paths: Iterable[Path]) -> frozenset[Path]:
    groups: dict[Path, set[str]] = {}
    for path in paths:
        groups.setdefault(path.parent, set()).add(path.name)
    ignored: set[Path] = set()
    for parent, names in groups.items():
        result = subprocess.run(
            ["git", "-C", str(parent), "check-ignore", "-z", "--stdin"],
            input=b"\0".join(os.fsencode(name) for name in sorted(names)) + b"\0",
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 1}:
            if b"not a git repository" in result.stderr:
                continue
            raise LoadoutError(f"could not verify original source privacy: {parent}")
        ignored.update(parent / os.fsdecode(name) for name in result.stdout.split(b"\0") if name)
    return frozenset(ignored)
