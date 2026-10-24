from __future__ import annotations

import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

from . import git_runner
from .errors import LoadoutError


def git(
    root: Path,
    *arguments: str,
    index: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    result = git_runner.run(root, *arguments, index=index)
    if check and result.returncode:
        raise LoadoutError(
            f"Git {' '.join(arguments[:2])} failed in {root}: "
            + result.stderr.decode(errors="replace").strip()
        )
    return result


def literal(path: str) -> str:
    if (
        not path
        or path == "."
        or "\0" in path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
    ):
        raise LoadoutError(f"invalid root-relative Git path: {path!r}")
    return ":(top,literal)" + path


def repository(path: Path) -> Path | None:
    """The work tree enclosing `path`, or None outside any repository."""
    return git_runner.toplevel(path)


def index_path(root: Path) -> Path:
    return Path(
        os.fsdecode(
            git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout
        ).strip()
    )


def tracked(root: Path, paths: Iterable[Path]) -> tuple[str, ...]:
    """The repository-relative names of `paths`, each spelled under `root`, that the index
    holds. A path is never resolved: under a link init replaces, that would name the file
    the link led to."""
    names = [
        path.relative_to(root).as_posix()
        for path in paths
        if path.is_relative_to(root) and path != root
    ]
    if not names:
        return ()
    listed = git(root, "ls-files", "-z", "--", *(literal(name) for name in names)).stdout
    return tuple(sorted(os.fsdecode(name) for name in listed.split(b"\0") if name))


def untrack(root: Path, names: tuple[str, ...]) -> None:
    """Drop generated files from the index, leaving them on disk; nothing else is staged."""
    git(root, "update-index", "--force-remove", "--", *names)


def new_source_repository(root: Path) -> Path:
    """Where `init global` creates the source's own repository."""
    if root in {Path(root.anchor), Path.home().resolve()}:
        raise LoadoutError(
            "new source repositories require a dedicated directory outside HOME and the "
            "filesystem root"
        )
    return root
