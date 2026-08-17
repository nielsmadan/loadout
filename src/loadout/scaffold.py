from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import LoadoutError
from .manifest import MANIFEST_NAME
from .project import (
    KNOWN_HARNESSES,
    ProjectConfig,
    load_project_config,
    project_config_path,
    project_outputs,
)

INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")

SOURCE_HEADER = """\
# Project permission rules, shared with everyone working in this repository.
#
# Personal rules that should not be committed go in permissions.local.toml.
# Both are merged; deny beats ask beats allow. Run `loadout sync` after editing.

[shell]
allow = []
ask = []
deny = []

[mcp]
allow = []
ask = []
deny = []
"""


def _tracked(root: Path, relative: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise LoadoutError(
            f"could not run git to check whether {relative} is tracked ({error}). "
            f"loadout init requires git to be installed and on PATH, so it can tell a "
            f"tracked instruction file from a scratch one before it touches anything."
        ) from error
    return result.returncode == 0


def _append_gitignore(root: Path, entries: list[str]) -> bool:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen = set(existing)
    additions: list[str] = []
    for entry in entries:
        if entry not in seen:
            additions.append(entry)
            seen.add(entry)
    if not additions:
        return False
    lines = existing + additions
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def init_project(
    root: Path, harnesses: tuple[str, ...], machine_config_path: Path | None = None
) -> list[str]:
    config = ProjectConfig(harnesses=harnesses)  # validates: non-empty, no duplicates, known

    config_path = project_config_path(root)
    project_dir = root / "loadout"
    already_initialised = config_path.exists()
    if already_initialised:
        existing = load_project_config(config_path)
        if set(existing.harnesses) != set(harnesses):
            raise LoadoutError(f"{config_path} already exists; this project is already initialised")

    actions: list[str] = []

    if not already_initialised:
        for name in INSTRUCTION_FILES:
            if (root / name).exists() and _tracked(root, name):
                actions.append(
                    f"note: {name} is tracked. loadout does not generate instruction "
                    f"files yet; when it does, you will need to move this into loadout/ "
                    f"first."
                )

        project_dir.mkdir(parents=True, exist_ok=True)

        quoted = ", ".join(f'"{h}"' for h in harnesses)
        config_path.write_text(f"harnesses = [{quoted}]\n", encoding="utf-8")
        actions.append(f"created loadout/config.toml ({', '.join(harnesses)})")

        (project_dir / "permissions.toml").write_text(SOURCE_HEADER, encoding="utf-8")
        actions.append("created loadout/permissions.toml")
        (project_dir / "permissions.local.toml").write_text("", encoding="utf-8")
        actions.append("created loadout/permissions.local.toml (personal, gitignored)")
    else:
        permissions_path = project_dir / "permissions.toml"
        if not permissions_path.is_file():
            permissions_path.write_text(SOURCE_HEADER, encoding="utf-8")
            actions.append("recreated loadout/permissions.toml (was missing)")

        local_path = project_dir / "permissions.local.toml"
        if not local_path.is_file():
            local_path.write_text("", encoding="utf-8")
            actions.append(
                "recreated loadout/permissions.local.toml (was missing, personal, gitignored)"
            )

    entries = ["loadout/permissions.local.toml"]
    entries += project_outputs(config.harnesses)
    if _append_gitignore(root, entries):
        actions.append(f"added {len(entries)} entries to .gitignore")

    if machine_config_path is not None and not machine_config_path.is_file():
        actions.append(
            "note: global scope is not set up on this machine. Run `loadout init --global` "
            "to set it up, or just use project scope as-is."
        )

    return actions


GLOBAL_MANIFEST_SKELETON = """\
# loadout's global manifest for this machine — instructions and permission
# rules shared across every project. Declare at least one
# [instructions.<agent>] or [permissions.<name>] block below, then run
# `loadout sync --global`.
#
# Fragments for an [instructions.*] target belong under instructions/
# relative to a source's path; permission rules belong under
# permissions.toml. Move the files created alongside this
# manifest into that layout once you have real content.

[[source]]
name = "global"
path = "."

# [instructions.claude]
# output       = "claude/CLAUDE.md"
# destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
# order        = ["intro"]

# [permissions.claude]
# output = "claude/settings.json"
# render = "claude"
"""

GLOBAL_SOURCE_HEADER = """\
# This machine's global permission rules, applied across every project that
# does not override them.
#
# Run `loadout sync --global` after editing.

[shell]
allow = []
ask = []
deny = []

[mcp]
allow = []
ask = []
deny = []
"""


def init_global(source_parent: Path, config_path: Path, force: bool = False) -> list[str]:
    if config_path.exists() and not force:
        raise LoadoutError(
            f"{config_path} already exists; this machine is already initialised for "
            f"global scope. Pass --force to reinitialise it."
        )

    loadout_dir = source_parent / "loadout"
    actions: list[str] = []

    if loadout_dir.is_dir():
        actions.append(f"{loadout_dir} already exists")
    else:
        loadout_dir.mkdir(parents=True)
        actions.append(f"created {loadout_dir}")

    manifest_file = loadout_dir / MANIFEST_NAME
    if manifest_file.is_file():
        actions.append(f"{manifest_file} already exists, left untouched")
    else:
        manifest_file.write_text(GLOBAL_MANIFEST_SKELETON, encoding="utf-8")
        actions.append(f"created {manifest_file}")

    permissions_file = loadout_dir / "permissions.toml"
    if permissions_file.is_file():
        actions.append(f"{permissions_file} already exists, left untouched")
    else:
        permissions_file.write_text(GLOBAL_SOURCE_HEADER, encoding="utf-8")
        actions.append(f"created {permissions_file}")

    instructions_dir = loadout_dir / "instructions"
    gitkeep = instructions_dir / ".gitkeep"
    if instructions_dir.is_dir():
        if gitkeep.is_file():
            actions.append(f"{instructions_dir} already exists, left untouched")
        else:
            gitkeep.write_text("", encoding="utf-8")
            actions.append(f"{instructions_dir} already exists; added missing .gitkeep")
    else:
        instructions_dir.mkdir()
        gitkeep.write_text("", encoding="utf-8")
        actions.append(f"created {instructions_dir}")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    overwriting = config_path.exists()
    config_path.write_text(f'source = "{loadout_dir.resolve()}"\n', encoding="utf-8")
    actions.append(f"{'overwrote' if overwriting else 'created'} {config_path}")

    actions.append(
        f"{manifest_file} declares a source but no targets yet; add an "
        f"[instructions.<agent>] or [permissions.<name>] block, then run "
        f"`loadout sync --global`."
    )
    return actions


def add_harness(root: Path, harness: str) -> list[str]:
    if harness not in KNOWN_HARNESSES:
        known = ", ".join(sorted(KNOWN_HARNESSES))
        raise LoadoutError(f"unknown harness {harness!r} (known: {known})")

    config_path = project_config_path(root)
    config = load_project_config(config_path)
    if harness in config.harnesses:
        raise LoadoutError(f"{harness} is already enabled in {config_path}")

    updated = ProjectConfig(harnesses=(*config.harnesses, harness))
    quoted = ", ".join(f'"{h}"' for h in updated.harnesses)
    config_path.write_text(f"harnesses = [{quoted}]\n", encoding="utf-8")

    actions = [f"enabled {harness} in loadout/config.toml"]
    entries = list(project_outputs([harness]))
    if entries and _append_gitignore(root, entries):
        actions.append(f"added {len(entries)} entries to .gitignore")
    actions.append("run `loadout sync` to generate its files")
    return actions
