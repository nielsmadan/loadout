"""Build a renderable root from tests/fixtures/.

Both conftest's `root` fixture and regenerate_expected.py go through here, so the tree
the expected output was generated from and the tree the tests render are the same tree
by construction. Two builders would drift.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected"

SOURCE_NAMES = ("loadout.toml", "permissions.toml", "instructions", "bases")

# permissions.opencode declares preserve = ["foreign"]. Seeding the key models a
# co-owner having already written it — without this the preserve path is never
# exercised, because there is nothing to carry through.
FOREIGN_OWNER = {"foreign": {"written-by": "another generator", "value": [1, 2]}}
PRESERVE_TARGET = "perm/opencode.json"


def build_root(destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_NAMES:
        source = FIXTURES / name
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)

    seeded = destination / PRESERVE_TARGET
    seeded.parent.mkdir(parents=True, exist_ok=True)
    seeded.write_text(json.dumps(FOREIGN_OWNER, indent=2) + "\n", encoding="utf-8")
    return destination
