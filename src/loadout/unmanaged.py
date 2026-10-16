"""Files loadout did not write, in folders a harness reads whole (ADR 0030).

Codex reads every file in its rules folder and each harness reads every folder in
its skills directory and every file in its agents directory, so a file there that no loadout root wrote takes effect beside
the source. It is flagged rather than absorbed: porting it is the /loadout skill's job.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from .destinations import resolve_destination
from .discovery import RUNTIME_DIRECTORIES, RUNTIME_FILES, UNMANAGED_NAMES
from .harnesses import DIRECTORY_SLICES
from .manifest import load_profile, manifest_path
from .output import Output
from .project import PROJECT_PRESET, PROTECTED, load_project_config, project_config_path
from .written import every_recorded_path

WHOLE_FOLDER_RENDERERS = frozenset({"codex", "codex-project"})


def managed_folders(
    root: Path, profile: str = "default"
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """The folders a declared slice writes into and its harness reads whole, and the
    links found in the way of a project's own, which are reported rather than entered."""
    folders: list[Path] = []
    links: list[Path] = []
    if manifest_path(root).is_file():
        manifest = load_profile(root, profile)
        folders += [
            resolve_destination(str(destination), f"{target.harness}.{slice_name}")
            for slice_name, targets in (("skills", manifest.skills), ("agents", manifest.agents))
            for target in targets
            for destination in target.destinations
        ]
        folders += [
            path.parent
            for target in manifest.permissions
            if target.renderer in WHOLE_FOLDER_RENDERERS
            for path in (
                *((root / str(target.path),) if target.path is not None else ()),
                *(
                    resolve_destination(str(d), f"permissions.{target.name}")
                    for d in target.destinations
                ),
            )
        ]
    if project_config_path(root).is_file():
        config = load_project_config(project_config_path(root))
        project: list[Path] = []
        project += [
            root / spec.output
            for harness in config.harnesses
            for slice_name in DIRECTORY_SLICES
            for spec in (PROJECT_PRESET[harness].get(slice_name),)
            if spec is not None and spec.output is not None
        ]
        project += [
            (root / spec.output).parent
            for harness in config.harnesses
            for spec in PROJECT_PRESET[harness].values()
            if spec.renderer in WHOLE_FOLDER_RENDERERS and spec.output is not None
        ]
        # A committed link can make a project folder any folder on the machine, or the
        # git dir, so a project folder is scanned only where sync would write.
        for folder in project:
            link = _first_link(root, folder)
            if link is not None:
                links.append(link)
            elif folder.relative_to(root).parts[0].casefold() not in PROTECTED:
                folders.append(folder)
    return tuple(dict.fromkeys(folders)), tuple(dict.fromkeys(links))


def _first_link(root: Path, folder: Path) -> Path | None:
    parts = folder.relative_to(root).parts
    return next(
        (
            root.joinpath(*parts[:depth])
            for depth in range(1, len(parts) + 1)
            if root.joinpath(*parts[:depth]).is_symlink()
        ),
        None,
    )


def _ignored(relative: Path) -> bool:
    # Dot entries are the OS's or the harness's own: .DS_Store, Codex's .system skills.
    return (
        any(part.startswith(".") or part in RUNTIME_DIRECTORIES for part in relative.parts)
        or relative.name in RUNTIME_FILES
        or relative.name in UNMANAGED_NAMES
    )


def _unaccounted(
    entry: Path, folder: Path, accounted: frozenset[Path], holding: frozenset[Path]
) -> list[Path]:
    if _ignored(entry.relative_to(folder)) or entry in accounted:
        return []
    if entry.is_symlink() or not entry.is_dir() or entry not in holding:
        return [entry]
    return [
        found
        for child in sorted(entry.iterdir())
        for found in _unaccounted(child, folder, accounted, holding)
    ]


def unmanaged_entries(
    root: Path, outputs: Mapping[Path, Output], profile: str = "default"
) -> list[Path]:
    """Each file, or whole folder, in a managed folder that no output and no root's
    record of what it wrote accounts for."""
    folders, links = managed_folders(root, profile)
    accounted = frozenset(outputs) | every_recorded_path(folders)
    return [*links, *_scan(folders, accounted)]


def _scan(folders: Iterable[Path], accounted: frozenset[Path]) -> list[Path]:
    # Every directory that holds an accounted path, so a lookup replaces a scan of them all.
    holding = frozenset(parent for path in accounted for parent in path.parents)
    return [
        found
        for folder in folders
        if folder.is_dir()
        for entry in sorted(folder.iterdir())
        for found in _unaccounted(entry, folder, accounted, holding)
    ]


def describe_unmanaged(path: Path) -> str:
    """The content a port would need, or for a folder the files it holds. A link is
    never followed: it may name anything, and only where it points is reported."""
    if path.is_symlink():
        return f"link -> {os.readlink(path)}\n"
    if path.is_dir():
        return "".join(
            f"{child.relative_to(path)}\n" for child in sorted(path.rglob("*")) if child.is_file()
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return f"{path.stat().st_size} bytes\n" if path.exists() else "(absent)\n"
