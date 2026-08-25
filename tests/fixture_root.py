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

SOURCE_NAMES = ("loadout.toml", "permissions.toml", "instructions", "settings", "bases")

# permissions.opencode declares preserve = ["foreign"]. Seeding the key models a
# co-owner having already written it — without this the preserve path is never
# exercised, because there is nothing to carry through.
FOREIGN_OWNER = {"foreign": {"written-by": "another generator", "value": [1, 2]}}
PRESERVE_TARGET = "perm/opencode.json"


PROJECT_FIXTURES = FIXTURES / "project"
PROJECT_HARNESSES = ("claude", "codex", "opencode", "pi")

# Vendored, so resolution reads no machine config and the expected tree stays
# machine-independent. It is what puts a template's contribution into the
# byte-compared output at all.
PROJECT_TEMPLATES = ("web",)

# Declared in reverse alphabetical order on purpose: sorted, this pair would come
# out the other way round, so the expected document proves the order is the
# config's rather than the directory listing's.
PROJECT_INSTRUCTIONS = ("testing", "conventions")

# Both preserve_foreign project targets, seeded so the carry-through path runs.
PROJECT_FOREIGN = {
    ".claude/settings.json": {"$schema": "https://example.invalid/claude.json"},
    "opencode.json": {"$schema": "https://example.invalid/opencode.json"},
}


def build_project_root(destination: Path) -> Path:
    directory = destination / "loadout"
    directory.mkdir(parents=True, exist_ok=True)
    quoted = ", ".join(f'"{harness}"' for harness in PROJECT_HARNESSES)
    templates = ", ".join(f'"{name}"' for name in PROJECT_TEMPLATES)
    fragments = ", ".join(f'"{name}"' for name in PROJECT_INSTRUCTIONS)
    (directory / "config.toml").write_text(
        f"harnesses = [{quoted}]\ntemplates = [{templates}]\ninstructions = [{fragments}]\n",
        encoding="utf-8",
    )
    for name in PROJECT_TEMPLATES:
        shutil.copytree(
            PROJECT_FIXTURES / "templates" / name,
            directory / "templates" / name,
            dirs_exist_ok=True,
        )
    shutil.copytree(
        PROJECT_FIXTURES / "instructions", directory / "instructions", dirs_exist_ok=True
    )
    shutil.copytree(PROJECT_FIXTURES / "skills", directory / "skills", dirs_exist_ok=True)
    for name in ("permissions.toml", "permissions.local.toml", "mcp.toml"):
        shutil.copy2(PROJECT_FIXTURES / name, directory / name)

    for name, document in PROJECT_FOREIGN.items():
        seeded = destination / name
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return destination


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
