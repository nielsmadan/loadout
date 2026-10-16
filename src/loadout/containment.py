"""Where loadout may read and write, decided in one place (review 3, batch 4.1).

A link committed to a repository, or added under a home directory, can point anywhere,
so every walker asks the same three questions: is a path inside a root, is it in a
folder loadout never writes, and which link, if any, sits on the way to it. The project
rules built on them, for what sync writes and what it reads, live here too.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from .errors import LoadoutError

# The project's source folder, defined here so containment depends on nothing above it.
PROJECT_DIR = "loadout"

# Never written, whatever a link says: git runs what is under its directory, and the
# outputs are rendered from the source.
PROTECTED = frozenset({".git", PROJECT_DIR})

# Leaf links from one output to another that a harness reads through.
READ_THROUGH = frozenset({(PurePosixPath("CLAUDE.md"), PurePosixPath("AGENTS.md"))})


def within(root: Path, path: Path, boundary: Path | None = None) -> PurePosixPath | None:
    """`path` relative to `root`, spelled through it or through its resolved `boundary`;
    None when it is under neither."""
    for base in (root, root.resolve() if boundary is None else boundary):
        if path.is_relative_to(base):
            return PurePosixPath(path.relative_to(base).as_posix())
    return None


def protected(relative: PurePosixPath) -> str | None:
    """The folder loadout never writes that `relative` starts in, if any. Case-folded:
    macOS and Windows file systems treat .GIT as .git."""
    first = relative.parts[0] if relative.parts else None
    return first if first is not None and first.casefold() in PROTECTED else None


def first_link(
    root: Path, path: Path, linked: Callable[[Path], bool] = Path.is_symlink
) -> Path | None:
    """The first link below `root` on the way to `path`, `path` itself included."""
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        if linked(current):
            return current
    return None


def no_symlinks(path: Path, root: Path | None = None) -> None:
    """Refuse a link at `path` or any folder above it, up to `root` and including it, or
    up to the file system's root when none is given."""
    current = path
    while True:
        if current.is_symlink():
            raise LoadoutError(f"path is a symlink: {current}")
        if current in (root, current.parent):
            break
        current = current.parent


def refuse_escaping(root: Path, paths: Iterable[Path], outputs: Iterable[Path] = ()) -> None:
    """Project scope writes only to real paths inside the project, never under the git
    dir or the source. A link committed to the repository can point anywhere, even
    inside it, so no link may sit between the root and a path. The one exception is
    `CLAUDE.md -> AGENTS.md` when this render writes both: Claude reads the shared
    instructions through it (ADR 0027)."""
    boundary = root.resolve()
    written = frozenset(
        relative for path in outputs if (relative := within(root, path, boundary)) is not None
    )
    # Many outputs share the folders above them, so each is asked about a link once.
    linked = functools.cache(Path.is_symlink)
    problems = [
        problem for path in paths if (problem := _unsafe(path, root, boundary, written, linked))
    ]
    if problems:
        raise LoadoutError(
            f"refusing to write {'; '.join(problems)}. Replace the link with a real file or "
            "folder, or remove it"
        )


def _outside(target: Path, boundary: Path) -> str:
    return "" if target.is_relative_to(boundary) else ", outside the project"


def _unsafe(
    path: Path,
    root: Path,
    boundary: Path,
    written: frozenset[PurePosixPath],
    linked: Callable[[Path], bool],
) -> str | None:
    relative = within(root, path, boundary)
    if relative is None:
        return f"{path}: outside the project"
    if (folder := protected(relative)) is not None:
        return f"{path}: under {folder}/, which loadout never writes"
    leaf = root / Path(relative)
    if (link := first_link(root, leaf.parent, linked)) is not None:
        target = link.resolve()
        return f"{path}: through the link {link} -> {target}{_outside(target, boundary)}"
    return _leaf_link(leaf, relative, root, boundary, written) if leaf.is_symlink() else None


def _leaf_link(
    leaf: Path,
    relative: PurePosixPath,
    root: Path,
    boundary: Path,
    written: frozenset[PurePosixPath],
) -> str | None:
    target = leaf.resolve()
    landing = within(root, target, boundary) if target.is_relative_to(boundary) else None
    if landing not in written:
        return f"{leaf} -> {target}, a link sync does not write through" + _outside(
            target, boundary
        )
    if (relative, landing) in READ_THROUGH:
        return None
    return (
        f"{leaf} -> {target}, a link from one output to another, so sync would overwrite one "
        "with the other"
    )


def refuse_external_links(root: Path, excluded: frozenset[str] = frozenset()) -> None:
    """Project inputs come from `loadout/` alone: a link out of it would read a file the
    repository keeps elsewhere, a gitignored `.env` or the git dir's credentials, into
    output. Links that stay inside it are followed; a folder in `excluded` inside a
    skill's own tree is never read, so it is not entered."""
    boundary = root.resolve()
    escaping: list[tuple[Path, Path]] = []
    visited: set[Path] = set()
    source = root / PROJECT_DIR
    if source.is_symlink():
        raise LoadoutError(
            f"{source} is a link; {PROJECT_DIR}/ must be a real folder, or sync would write "
            "its outputs into whatever the link names"
        )
    inputs = boundary / PROJECT_DIR
    # Each folder still to list, with where it lands. Only a link can land outside
    # `loadout/`, so only a link is resolved and checked (review 3, batch 3.5); anything
    # else lands beside its folder's own landing place.
    pending = [(source, inputs)] if source.is_dir() else []
    while pending:
        current, resolved = pending.pop()
        if resolved in visited:
            continue
        visited.add(resolved)
        with os.scandir(current) as listing:
            entries = sorted(listing, key=lambda entry: entry.name, reverse=True)
        for entry in entries:
            path = current / entry.name
            if entry.name in excluded and _in_a_skill(path.relative_to(source).parts):
                continue
            if entry.is_symlink():
                target = path.resolve()
                if not target.is_relative_to(inputs):
                    escaping.append((path, target))
                elif target.is_dir():
                    pending.append((path, target))
            elif entry.is_dir(follow_symlinks=False):
                pending.append((path, resolved / entry.name))
    if escaping:
        listed = "; ".join(
            f"{link} -> {target}{_landing(target, boundary)}" for link, target in escaping
        )
        raise LoadoutError(
            f"refusing to read through a link out of {PROJECT_DIR}/: {listed}. Copy the file "
            f"into {PROJECT_DIR}/, or point the link inside it"
        )


def _landing(target: Path, boundary: Path) -> str:
    """Where a link out of `loadout/` lands, for the refusal."""
    relative = within(boundary, target, boundary)
    if relative is None:
        return ", outside the project"
    # The git dir holds credentials and runs hooks.
    if relative.parts and relative.parts[0].casefold() == ".git":
        return ", into .git"
    return f", out of {PROJECT_DIR}/"


def _in_a_skill(parts: tuple[str, ...]) -> bool:
    """Below `skills/<name>/`, a project's own or a template's: the skill's name itself
    is not inside its tree, so a skill named like an excluded folder is still read."""
    return any(part == "skills" and len(parts) > index + 2 for index, part in enumerate(parts))
