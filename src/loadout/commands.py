from __future__ import annotations

import difflib
import sys
from pathlib import Path

from .emit import check_all, manifest_path, write_all
from .errors import LoadoutError
from .manifest import load_manifest
from .resolve import resolve_fragment


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


def cmd_explain(root: Path, name: str) -> int:
    _require_manifest(root)
    manifest = load_manifest(manifest_path(root))
    item = resolve_fragment(manifest.sources, name)

    users: list[str] = []
    unresolved: list[str] = []
    for target in manifest.targets:
        matched = False
        for fragment in target.fragments:
            try:
                if resolve_fragment(manifest.sources, fragment).path == item.path:
                    matched = True
            except LoadoutError:
                unresolved.append(f"{target.path}: {fragment}")
        if matched:
            users.append(str(target.path))

    print(f"{item.name}")
    print(f"  source: {item.source}")
    print(f"  file:   {item.path}")
    if users:
        print("  used by:")
        for target_path in users:
            print(f"    {target_path}")
    else:
        print("  used by: (no target lists it)")
    if unresolved:
        print("  warning: other fragments in this manifest do not resolve:", file=sys.stderr)
        for entry in unresolved:
            print(f"    {entry}", file=sys.stderr)
    return 0
