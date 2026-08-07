from __future__ import annotations

import subprocess
from pathlib import Path

from .errors import LoadoutError
from .project import (
    KNOWN_HARNESSES,
    PRESET,
    ProjectConfig,
    load_project_config,
    project_config_path,
    project_targets,
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
    additions = [e for e in entries if e not in existing]
    if not additions:
        return False
    lines = existing + additions
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def init_project(root: Path, harnesses: tuple[str, ...]) -> list[str]:
    bad = sorted(set(harnesses) - KNOWN_HARNESSES)
    if bad:
        known = ", ".join(sorted(KNOWN_HARNESSES))
        raise LoadoutError(f"unknown harness(es) {', '.join(bad)} (known: {known})")
    if not harnesses:
        raise LoadoutError("at least one harness is required")

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
                raise LoadoutError(
                    f"{name} is tracked by git. loadout will generate project instruction "
                    f"files in a later milestone, and generated files are gitignored — "
                    f"initialising now would leave a tracked file that a future sync "
                    f"overwrites. Move its content into loadout/ and untrack it first, "
                    f"or remove it from this repository."
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

    config = ProjectConfig(harnesses=harnesses)
    entries = ["loadout/permissions.local.toml"]
    entries += [str(t.path) for t in project_targets(config)]
    if _append_gitignore(root, entries):
        actions.append(f"added {len(entries)} entries to .gitignore")

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
    entries = [str(t.path) for t in PRESET[harness]]
    if entries and _append_gitignore(root, entries):
        actions.append(f"added {len(entries)} entries to .gitignore")
    actions.append("run `loadout sync` to generate its files")
    return actions
