from __future__ import annotations

import difflib
import sys
from pathlib import Path

from .emit import check_all, manifest_path, write_all
from .errors import LoadoutError


def _require_manifest(root: Path) -> None:
    path = manifest_path(root)
    if not path.is_file():
        raise LoadoutError(f"manifest not found: {path}")


def cmd_sync(root: Path) -> int:
    _require_manifest(root)
    for path in write_all(root):
        print(f"wrote {path.relative_to(root)}")
    return 0


def cmd_check(root: Path) -> int:
    _require_manifest(root)
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
