#!/usr/bin/env python3
"""Regenerate tests/fixtures/expected/ from tests/fixtures/.

Expected output is not frozen (ADR 0009) — a deliberate rendering change is landed by
running this and reviewing the diff. The review is the protection, so this refuses to run
when the expected tree already has unstaged changes: a regeneration must be its own
reviewable commit, never something that rides along inside a feature diff.

    python3 tests/regenerate_expected.py            # rewrite the expected tree
    python3 tests/regenerate_expected.py --check    # exit 1 if it would change anything

Every profile is rendered. A destination outside the root is written under
expected/<profile>/destinations/ by file name, so profile-swapped documents that share a
destination are both captured.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fixture_root import EXPECTED, FIXTURES, build_project_root, build_root
from loadout.emit import Copied, declared_profiles, render_global, render_project


def render_everything() -> dict[str, str]:
    files: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as directory:
        root = build_root(Path(directory))
        for profile in sorted(declared_profiles(root)):
            for path, content in render_global(root, profile).items():
                try:
                    key = f"{profile}/{path.relative_to(root)}"
                except ValueError:
                    key = f"{profile}/destinations/{path.name}"
                if key in files and files[key] != content:
                    raise SystemExit(f"two targets disagree on {key}")
                files[key] = content

    with tempfile.TemporaryDirectory() as directory:
        root = build_project_root(Path(directory))
        for path, content in render_project(root).items():
            # A skill's supporting files are named rather than rendered, so the
            # expected tree holds their bytes: the comparison is about what lands
            # at the destination, not about how it got there.
            files[f"project/{path.relative_to(root)}"] = (
                content.source.read_text(encoding="utf-8")
                if isinstance(content, Copied)
                else content
            )
    return files


def unstaged_changes() -> bool:
    result = subprocess.run(
        ["git", "diff", "--quiet", "--", str(EXPECTED)],
        cwd=FIXTURES.parent.parent,
        check=False,
    )
    return result.returncode != 0


def main(argv: list[str]) -> int:
    checking = argv == ["--check"]
    if argv and not checking:
        print(f"usage: {Path(__file__).name} [--check]", file=sys.stderr)
        return 2

    files = render_everything()

    if checking:
        drift = [
            name
            for name, content in files.items()
            if not (EXPECTED / name).is_file()
            or (EXPECTED / name).read_text(encoding="utf-8") != content
        ]
        stale = [
            str(path.relative_to(EXPECTED))
            for path in sorted(EXPECTED.rglob("*"))
            if path.is_file() and str(path.relative_to(EXPECTED)) not in files
        ]
        if not drift and not stale:
            print("expected output is up to date")
            return 0
        for name in drift:
            print(f"WOULD REWRITE: {name}", file=sys.stderr)
        for name in stale:
            print(f"WOULD DELETE:  {name}", file=sys.stderr)
        print("\nrun tests/regenerate_expected.py, then review the diff", file=sys.stderr)
        return 1

    if EXPECTED.is_dir() and unstaged_changes():
        print(
            f"{EXPECTED} has unstaged changes — commit or discard them first so this\n"
            "regeneration lands as its own reviewable diff.",
            file=sys.stderr,
        )
        return 2

    if EXPECTED.is_dir():
        shutil.rmtree(EXPECTED)
    for name, content in sorted(files.items()):
        destination = EXPECTED / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    print(f"wrote {len(files)} file(s) to {EXPECTED}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
