"""Build a renderable root from tests/fixtures/.

Both conftest's `root` fixture and regenerate_expected.py go through here, so the tree
the expected output was generated from and the tree the tests render are the same tree
by construction. Two builders would drift.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from loadout.output import Copied
from loadout.stealth_documents import OwnedJson, apply_owned
from loadout.stealth_manifest import load_stealth_manifest
from loadout.stealth_render import gather_facts, render_stealth
from loadout.stealth_seams import serialize_json

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected"

SOURCE_NAMES = ("loadout.toml", "permissions.toml", "mcp.toml", "instructions", "settings", "bases")

# permissions.opencode declares preserve = ["foreign"]. Seeding the key models a
# co-owner having already written it — without this the preserve path is never
# exercised, because there is nothing to carry through.
FOREIGN_OWNER = {"foreign": {"written-by": "another generator", "value": [1, 2]}}
PRESERVE_TARGET = "perm/opencode.json"


PROJECT_FIXTURES = FIXTURES / "project"
PROJECT_HARNESSES = ("claude", "codex", "droid", "opencode", "pi")

# Vendored, so resolution reads no machine config and the expected tree stays
# machine-independent. It is what puts a template's contribution into the
# byte-compared output at all.
PROJECT_TEMPLATES = ("web",)

# Declared in reverse alphabetical order on purpose: sorted, this pair would come
# out the other way round, so the expected document proves the order is the
# config's rather than the directory listing's.
PROJECT_INSTRUCTIONS = ("testing", "conventions")

# Every preserve_foreign project target, seeded so the carry-through path runs.
PROJECT_FOREIGN = {
    ".claude/settings.json": {"$schema": "https://example.invalid/claude.json"},
    ".factory/settings.json": {"model": "custom-model"},
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
    shutil.copytree(PROJECT_FIXTURES / "agents", directory / "agents", dirs_exist_ok=True)
    for name in (
        "permissions.toml",
        "permissions.local.toml",
        "mcp.toml",
        "hooks.json",
        "plugins.json",
    ):
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


STEALTH_FIXTURES = FIXTURES / "stealth"
STEALTH_NAME = "fixture"


def build_stealth(destination: Path) -> tuple[Path, Path]:
    folder = destination / "stealth"
    shutil.copytree(STEALTH_FIXTURES, folder)
    target = destination / "target"
    target.mkdir()
    (target / "AGENTS.md").write_text("Team instructions.\n", encoding="utf-8")
    return folder, target


def _stealth_text(output: str | Copied | OwnedJson) -> str:
    if isinstance(output, OwnedJson):
        return serialize_json(apply_owned(None, output, None).document)
    if isinstance(output, Copied):
        return output.read_bytes().decode("utf-8")
    return output


def render_stealth_expected(destination: Path) -> dict[str, str]:
    folder, target = build_stealth(destination)
    rendered = render_stealth(
        load_stealth_manifest(folder), STEALTH_NAME, target, gather_facts(target)
    )
    files = {
        f"stealth/target/{path.relative_to(target)}": _stealth_text(output)
        for path, output in rendered.files.items()
    }
    files.update(
        {
            f"stealth/shared/{path.relative_to(folder)}": text
            for path, text in rendered.shared.items()
        }
    )
    if rendered.claude_servers:
        files["stealth/claude-servers.json"] = serialize_json(rendered.claude_servers)
    spellings = sorted({str(destination.resolve()), str(destination)}, key=len, reverse=True)
    return {name: _with_placeholder(text, spellings) for name, text in files.items()}


def _with_placeholder(text: str, spellings: list[str]) -> str:
    for spelling in spellings:
        text = text.replace(spelling, "$TMP")
    return text
