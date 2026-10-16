from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from .errors import LoadoutError


def _repository(directory: Path, cache: dict[Path, Path | None]) -> Path | None:
    """The work tree holding `directory`, found by its `.git` without running Git."""
    if directory not in cache:
        cache[directory] = (
            directory
            if (directory / ".git").exists()
            else None
            if directory.parent == directory
            else _repository(directory.parent, cache)
        )
    return cache[directory]


def _by_repository(paths: Iterable[Path]) -> dict[Path, dict[str, Path]]:
    """Each path's repository-relative name, grouped by repository, so one Git run
    answers for every path in it. The parent is resolved, as `git -C` would."""
    cache: dict[Path, Path | None] = {}
    groups: dict[Path, dict[str, Path]] = {}
    for path in paths:
        parent = path.parent.resolve()
        repository = _repository(parent, cache)
        if repository is not None:
            name = (parent / path.name).relative_to(repository).as_posix()
            groups.setdefault(repository, {})[name] = path
    return groups


def _names(output: bytes) -> list[str]:
    return [os.fsdecode(name) for name in output.split(b"\0") if name]


def ignored_paths(paths: Iterable[Path]) -> frozenset[Path]:
    ignored: set[Path] = set()
    for repository, names in _by_repository(paths).items():
        result = subprocess.run(
            ["git", "-C", str(repository), "check-ignore", "-z", "--stdin"],
            input=b"".join(os.fsencode(name) + b"\0" for name in sorted(names)),
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 1}:
            if b"not a git repository" in result.stderr:
                continue
            raise LoadoutError(f"could not verify original source privacy: {repository}")
        ignored.update(names[name] for name in _names(result.stdout) if name in names)
    return frozenset(ignored)


def tracked_paths(paths: Iterable[Path]) -> frozenset[Path]:
    tracked: set[Path] = set()
    for repository, names in _by_repository(paths).items():
        # One listing of the index, intersected here, rather than a pathspec per path.
        result = subprocess.run(
            ["git", "-C", str(repository), "ls-files", "-z"], capture_output=True, check=False
        )
        if result.returncode:
            if b"not a git repository" in result.stderr:
                continue
            raise LoadoutError(f"could not read which originals Git tracks: {repository}")
        tracked.update(names[name] for name in _names(result.stdout) if name in names)
    return frozenset(tracked)
