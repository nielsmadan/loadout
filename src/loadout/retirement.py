"""Retiring what sync wrote before and no longer renders (D-03).

A candidate is a destination in the written record that this run does not render.
Whether it is still loadout's own output is the guard's question, answered once in
`commands.unaccepted_outputs`; nothing here judges content.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .emit import atomic_write, describe_file
from .output import Output
from .skills import SKILL_DOCUMENT
from .surgery import apply_json, apply_toml
from .written import WrittenEntry


def orphaned(
    written: Mapping[Path, WrittenEntry], outputs: Mapping[Path, Output]
) -> dict[Path, WrittenEntry]:
    return {path: entry for path, entry in written.items() if path not in outputs}


def stripped(path: Path, entry: WrittenEntry, existing: str) -> str:
    """The shared file without the keys loadout recorded writing, and nothing else."""
    if path.suffix == ".json":
        return apply_json(existing, frozenset(entry.owned), "{}")
    return apply_toml(existing, frozenset(entry.owned), "")


def orphan_drift(orphans: Mapping[Path, WrittenEntry]) -> list[tuple[Path, str, str]]:
    """What retiring would change, in check's (path, on disk, wanted) shape."""
    drift: list[tuple[Path, str, str]] = []
    for path, entry in sorted(orphans.items()):
        if not path.is_file():
            continue
        if entry.kind == "merged":
            existing = path.read_text(encoding="utf-8", newline="")
            wanted = stripped(path, entry, existing)
            if wanted != existing:
                drift.append((path, existing, wanted))
        elif entry.kind == "text":
            drift.append((path, path.read_text(encoding="utf-8", newline=""), ""))
        else:
            drift.append((path, describe_file(path), "(absent)\n"))
    return drift


def retire(orphans: Mapping[Path, WrittenEntry], recorded: Iterable[Path]) -> list[Path]:
    """Delete each orphaned file, or strip loadout's keys from a shared one.

    Returns the paths changed on disk. An orphan already gone needs nothing.
    """
    skill_folders = {path.parent for path in recorded if path.name == SKILL_DOCUMENT}
    retired: list[Path] = []
    for path, entry in sorted(orphans.items()):
        if not path.is_file():
            continue
        if entry.kind == "merged":
            existing = path.read_text(encoding="utf-8", newline="")
            wanted = stripped(path, entry, existing)
            if wanted == existing:
                continue
            atomic_write(path, wanted)
        else:
            path.unlink()
            _prune_skill_folder(path, skill_folders)
        retired.append(path)
    return retired


def _prune_skill_folder(path: Path, skill_folders: set[Path]) -> None:
    """Remove directories the deletion emptied, up to and including the skill folder.

    A folder still holding a file loadout never wrote is not empty, so it stays.
    """
    folder = next((parent for parent in path.parents if parent in skill_folders), None)
    if folder is None:
        return
    for directory in path.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        if directory == folder:
            return
