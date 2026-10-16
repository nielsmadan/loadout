"""Telling another worktree of the registered source's repository from any other folder
(review 3, batch 2.2): it shares the source's repository, so its hooks are the source's,
but it is not the registered folder and must not render global scope."""

from __future__ import annotations

import os
from pathlib import Path

from . import git_runner
from .machine import Registration, is_registered, registration


def _worktree(path: Path) -> tuple[Path, Path] | None:
    """The top level of the worktree holding `path` and its repository's common dir, or
    None outside a repository. Asked through the one git runner, so a hook's `GIT_DIR`
    cannot answer for a folder that is not its own (review 4, batch 1.1)."""
    result = git_runner.run(
        path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"
    )
    match os.fsdecode(result.stdout).splitlines():
        case [top, common] if not result.returncode:
            return Path(top).resolve(), Path(common).resolve()
        case _:
            return None


def linked_worktree_source(root: Path, found: Registration | None = None) -> Path | None:
    """The registered source, when `root` is in another worktree of its repository:
    the same repository, a different worktree, whichever of the two is the main one.
    `found` is a registration already read, so a caller decides from one read."""
    source = (found or registration()).source
    if source is None or is_registered(root, source) or not root.is_dir() or not source.is_dir():
        return None
    here, there = _worktree(root), _worktree(source)
    if here is None or there is None:
        return None
    return source if here[1] == there[1] and here[0] != there[0] else None
