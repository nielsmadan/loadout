"""Templates — shared configuration for a kind of project.

A template is a source (spec 3): a named bundle of the portable slices that a
project opts into, merged beneath everything the project itself declares. It
resolves by **name**, never by path, because a path in a committed file means
nothing on a colleague's machine and less in CI.

Declared and vendored are the same source resolved from two places, not a primary
path and an escape hatch. What makes vendoring safe is the recorded content hash:
it answers the one question `sync` has to ask before it overwrites anything.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import LoadoutError
from .machine import load_machine_config, machine_config_path
from .manifest import load_manifest, manifest_path
from .project import PROJECT_DIR
from .resolve import ResolvedItem, Slice, resolve_item
from .skills import EXCLUDED_DIRECTORIES, EXCLUDED_NAMES, EXCLUDED_SUFFIXES
from .sources import Source

HASH_PREFIX = "sha256:"

TEMPLATES_SUBDIR = "templates"
TEMPLATES = Slice(use="templates", subdir=TEMPLATES_SUBDIR, suffix="", directory=True)

# The `source` a vendored template reports. Parenthesised so it cannot collide
# with a real source name, which is a bare identifier.
VENDORED = "(vendored)"


def vendored_root(root: Path) -> Path:
    """Vendored templates get a directory of their own, never merged into the
    project's own fragments — otherwise nothing could later tell template-owned
    content from content you wrote, and sync would be impossible."""
    return root / PROJECT_DIR / TEMPLATES_SUBDIR


def vendored_path(root: Path, name: str) -> Path:
    return vendored_root(root) / name


def declared_sources(config_path: Path | None = None) -> tuple[Source, ...]:
    """Every source the machine's global manifest declares that offers templates.

    Project scope carries no `[[source]]` list of its own, and must not: a path in
    a committed file is wrong for everyone who is not its author. So a declared
    name resolves through the machine config, which is where this machine's paths
    already live (ADR 0010).
    """
    path = machine_config_path() if config_path is None else config_path
    machine = load_machine_config(path)
    if machine is None:
        raise LoadoutError(
            f"no machine config at {path}, so a declared template has nowhere to "
            f"resolve from; run `loadout init --global`, or vendor the template"
        )
    manifest = load_manifest(manifest_path(machine.source))
    return tuple(s for s in manifest.sources if TEMPLATES.use in s.use)


def resolve_template(name: str, root: Path, config_path: Path | None = None) -> ResolvedItem:
    """A template name, resolved the way a fragment name is — one level up.

    A vendored copy stops resolution before the machine config is even read. That
    is what lets a clone build without the template repo, and it is why switching
    between declared and vendored is not a migration: same source, same list,
    a different place it resolves from.
    """
    local = vendored_path(root, name)
    if local.is_dir():
        return ResolvedItem(name=name, source=VENDORED, path=local)

    sources = declared_sources(config_path)
    try:
        return resolve_item(sources, name, TEMPLATES)
    except LoadoutError as error:
        searched = ", ".join(str(s.path / TEMPLATES_SUBDIR / name) for s in sources)
        where = searched or "(no source offers templates)"
        raise LoadoutError(f"{error} Searched {local} and {where}.") from error


def _excluded(relative: Path) -> bool:
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    return relative.name in EXCLUDED_NAMES or relative.suffix in EXCLUDED_SUFFIXES


def template_files(tree: Path) -> tuple[Path, ...]:
    """Every content file in a template, relative to its root, sorted.

    Build output is skipped for the reason a skill skips it: a template that once
    had a `__pycache__` in it would otherwise never compare equal to the same
    template checked out fresh.
    """
    if not tree.is_dir():
        return ()
    return tuple(
        sorted(
            item.relative_to(tree)
            for item in tree.rglob("*")
            if item.is_file() and not _excluded(item.relative_to(tree))
        )
    )


def tree_hash(tree: Path) -> str:
    """A content hash of a template, independent of where the tree sits.

    Path-independent by construction — only paths *relative* to the template root
    are hashed — so vendoring does not change the hash, which is what lets one
    recorded value compare a copy against its upstream.

    A git SHA would not do: a template may come from a plain directory with no
    repository behind it.
    """
    digest = hashlib.sha256()
    for relative in template_files(tree):
        path = tree / relative
        payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
        digest.update(b"\0")
        # The length pins the boundary, so no arrangement of bytes across two
        # files can collide with a different arrangement across two others.
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return HASH_PREFIX + digest.hexdigest()
