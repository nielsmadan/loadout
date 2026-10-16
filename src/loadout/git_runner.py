"""The one way loadout starts git (review 4, batch 1.1).

Git exports `GIT_DIR` and its other repository-local variables to the hooks it runs, and a
git started from a hook inherits them: `-C` then names a directory, but the repository is
still the hook's. So every git loadout starts drops those variables first, and `-C` finds
the repository from the directory alone. `check --staged` keeps `GIT_INDEX_FILE`, since the
index it was given is the one it inspects.
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .errors import LoadoutError

# What `git rev-parse --local-env-vars` printed when it could not run: git 2.54's list.
_FALLBACK = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG",
    "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT",
    "GIT_OBJECT_DIRECTORY",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE",
    "GIT_PREFIX",
    "GIT_SHALLOW_FILE",
    "GIT_COMMON_DIR",
)


@functools.cache
def local_variables() -> tuple[str, ...]:
    """The variables git treats as belonging to one repository, from git itself."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--local-env-vars"], capture_output=True, check=False
        )
    except OSError:
        return _FALLBACK
    names = tuple(os.fsdecode(result.stdout).split())
    return names if result.returncode == 0 and names else _FALLBACK


# Which index a git reads: the repository's own (None), the one git was given ("given"),
# which `check --staged` inspects, or a file of loadout's own.
Index = Path | Literal["given"] | None


def environment(*, index: Index = None, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment a git loadout starts runs in: none of a hook's repository, and
    the index `index` names."""
    source = dict(os.environ if base is None else base)
    given = source.get("GIT_INDEX_FILE")
    cleaned = {name: value for name, value in source.items() if name not in local_variables()}
    cleaned["GIT_OPTIONAL_LOCKS"] = "0"
    if index == "given":
        if given:
            cleaned["GIT_INDEX_FILE"] = str(Path(given).absolute())
    elif index is not None:
        cleaned["GIT_INDEX_FILE"] = str(index)
    return cleaned


def run(
    root: Path,
    *arguments: str,
    data: bytes | None = None,
    index: Index = None,
) -> subprocess.CompletedProcess[bytes]:
    """`git -C root …`, its result returned whatever the exit code. Without git to run,
    the result is a failure naming it: the guard does not need git (review 5, batch 2.1)."""
    command = ["git", "-C", str(root), *arguments]
    try:
        return subprocess.run(
            command,
            input=data,
            capture_output=True,
            env=environment(index=index),
            check=False,
        )
    except OSError as error:
        return subprocess.CompletedProcess(command, NOT_RUN, b"", _not_run(error).encode())


def start(root: Path, *arguments: str, index: Index = None) -> subprocess.Popen[bytes]:
    """`git -C root …` as a process to stream to and from, such as `cat-file --batch`."""
    try:
        return subprocess.Popen(
            ["git", "-C", str(root), *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env=environment(index=index),
        )
    except OSError as error:
        raise LoadoutError(_not_run(error)) from error


# The exit code a shell gives a command it cannot find.
NOT_RUN = 127


def _not_run(error: OSError) -> str:
    return f"git could not be run: {error.strerror or error}; install git or put it on PATH"


def output(root: Path, *arguments: str, data: bytes | None = None, index: Index = None) -> bytes:
    """`git -C root …`'s output, or a LoadoutError naming the command that failed."""
    result = run(root, *arguments, data=data, index=index)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise LoadoutError(f"Git {' '.join(arguments[:2])} failed: {detail}")
    return result.stdout


def toplevel(path: Path) -> Path | None:
    """The top level of the worktree holding `path`, resolved: None outside a repository
    or without git to ask, and a LoadoutError for any other failure, such as a repository
    git refuses to read. The one probe every caller uses (review 5, batch 3.3)."""
    result = run(path, "rev-parse", "--show-toplevel")
    if result.returncode == NOT_RUN or b"not a git repository" in result.stderr:
        return None
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise LoadoutError(f"cannot inspect Git repository {path}: {detail}")
    return Path(os.fsdecode(result.stdout).strip()).resolve()
