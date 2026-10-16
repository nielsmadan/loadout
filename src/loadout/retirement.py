"""Retiring what sync wrote before and no longer renders (ADR 0022).

A candidate is a destination in the written record that this run does not render.
Whether it is still loadout's own output is the guard's question, answered once in
`sync.unaccepted_outputs`; nothing here judges content.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .files import (
    Drift,
    UnparsableDocument,
    atomic_write,
    describe_file,
    document_format,
    read_document,
    shown_document,
)
from .output import Output
from .skills import SKILL_DOCUMENT
from .surgery import EMPTY, apply
from .written import WrittenEntry, owned_values


def orphaned(
    written: Mapping[Path, WrittenEntry], outputs: Mapping[Path, Output]
) -> dict[Path, WrittenEntry]:
    return {path: entry for path, entry in written.items() if path not in outputs}


def stripped(path: Path, entry: WrittenEntry, existing: str) -> str:
    """The shared file without the keys loadout recorded writing, and nothing else."""
    format_name = document_format(path)
    return apply(existing, frozenset(entry.owned), EMPTY[format_name], format_name=format_name)


def orphan_drift(orphans: Mapping[Path, WrittenEntry]) -> list[Drift]:
    """What retiring would change, as drift."""
    drift: list[Drift] = []

    def retires(path: Path, actual: str, expected: str) -> None:
        drift.append(Drift(path=path, actual=actual, expected=expected, kind="orphaned"))

    for path, entry in sorted(orphans.items()):
        if not path.is_file():
            continue
        if entry.kind == "merged":
            existing = read_document(path)
            held = (
                None
                if existing is None
                else owned_values(existing, entry.owned, document_format(path))
            )
            if existing is None or held is None:
                # Retiring strips keys, never the document, so no diff says what it does.
                drift.append(Drift(path=path, actual="", expected="", kind="unparsable"))
                continue
            remaining = stripped(path, entry, existing)
            if remaining != existing:
                retires(path, existing, remaining)
        elif entry.kind == "text":
            retires(path, read_document(path) or shown_document(path), "")
        else:
            retires(path, describe_file(path), "(absent)\n")
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
            existing = read_document(path)
            if existing is None:
                raise UnparsableDocument(path, document_format(path), orphaned=True)
            remaining = stripped(path, entry, existing)
            if remaining == existing:
                continue
            atomic_write(path, remaining)
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
