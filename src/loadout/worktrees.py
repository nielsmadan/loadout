"""Telling a linked worktree of the registered source from any other folder (review 3,
batch 2.2): it shares the source's repository, so its hooks are the source's, but it is not
the registered folder and must not render global scope."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .machine import registered_source


def _git_dirs(path: Path) -> tuple[Path, Path] | None:
    """The git dir and common dir of the repository holding `path`, or None outside one."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                "--git-common-dir",
            ],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    match os.fsdecode(result.stdout).splitlines():
        case [git_dir, common_dir] if not result.returncode:
            return Path(git_dir).resolve(), Path(common_dir).resolve()
        case _:
            return None


def linked_worktree_source(root: Path) -> Path | None:
    """The registered source when `root` is in a linked worktree of its repository."""
    source = registered_source()
    if source is None or not root.is_dir():
        return None
    here = _git_dirs(root)
    if here is None or here[0] == here[1]:
        return None
    registered = _git_dirs(source) if source.is_dir() else None
    return source if registered is not None and registered[1] == here[1] else None
