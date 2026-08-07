from __future__ import annotations

import difflib
import sys
from pathlib import Path

from .emit import check_all, write_all
from .errors import LoadoutError
from .manifest import MANIFEST_NAME, load_manifest, manifest_path
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR, project_config_path
from .resolve import resolve_fragment
from .scaffold import add_harness, init_project


def cmd_init(root: Path, harnesses: tuple[str, ...]) -> int:
    for action in init_project(root, harnesses):
        print(action)
    print("\nEdit loadout/permissions.toml, then run `loadout sync`.")
    return 0


def cmd_harness_add(root: Path, harness: str) -> int:
    for action in add_harness(root, harness):
        print(action)
    return 0


def cmd_sync(root: Path) -> int:
    for path in write_all(root):
        print(f"wrote {path.relative_to(root)}")
    return 0


def cmd_check(root: Path) -> int:
    drift = check_all(root)
    if not drift:
        print("generated files are up to date")
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
        f"\n{len(drift)} generated file(s) out of sync — run `loadout sync`.",
        file=sys.stderr,
    )
    return 1


def cmd_explain(root: Path, name: str) -> int:
    root_manifest = manifest_path(root)
    if not root_manifest.is_file() and project_config_path(root).is_file():
        raise LoadoutError(
            f"explain covers instruction fragments, which are global scope only "
            f"({MANIFEST_NAME}); this repo has project scope only "
            f"({PROJECT_DIR}/{PROJECT_CONFIG_NAME})."
        )
    manifest = load_manifest(root_manifest)
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
