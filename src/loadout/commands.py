from __future__ import annotations

import difflib
import sys
from pathlib import Path

from .emit import check_all, fragments_dir, write_all
from .errors import LoadoutError


def _require_fragments(root: Path) -> None:
    directory = fragments_dir(root)
    if not directory.is_dir():
        raise LoadoutError(f"fragments directory not found: {directory}")


def cmd_sync(root: Path) -> int:
    _require_fragments(root)
    for path in write_all(root):
        print(f"wrote {path.relative_to(root)}")
    return 0


def cmd_check(root: Path) -> int:
    _require_fragments(root)
    drift = check_all(root)
    if not drift:
        print("global instruction files are up to date")
        return 0
    for path, actual, expected in drift:
        rel = path.relative_to(root)
        print(f"DRIFT: {rel}", file=sys.stderr)
        sys.stderr.writelines(
            difflib.unified_diff(
                actual.splitlines(keepends=True),
                expected.splitlines(keepends=True),
                fromfile=f"{rel} (on disk)",
                tofile=f"{rel} (expected)",
            )
        )
    print(
        f"\n{len(drift)} global instruction file(s) out of sync — run `loadout sync`.",
        file=sys.stderr,
    )
    return 1
