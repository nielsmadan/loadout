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

from .skills import EXCLUDED_DIRECTORIES, EXCLUDED_NAMES, EXCLUDED_SUFFIXES

HASH_PREFIX = "sha256:"


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
