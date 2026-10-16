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


def environment(
    *, keep_index: bool = False, index: Path | None = None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment a git loadout starts runs in: none of a hook's repository, and
    the index named by `index`, or the one it was given when `keep_index`."""
    source = dict(os.environ if base is None else base)
    given = source.get("GIT_INDEX_FILE")
    cleaned = {name: value for name, value in source.items() if name not in local_variables()}
    cleaned["GIT_OPTIONAL_LOCKS"] = "0"
    if index is not None:
        cleaned["GIT_INDEX_FILE"] = str(index)
    elif keep_index and given:
        cleaned["GIT_INDEX_FILE"] = str(Path(given).absolute())
    return cleaned


def run(
    root: Path,
    *arguments: str,
    data: bytes | None = None,
    keep_index: bool = False,
    index: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """`git -C root …`, its result returned whatever the exit code."""
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=data,
        capture_output=True,
        env=environment(keep_index=keep_index, index=index),
        check=False,
    )


def start(root: Path, *arguments: str, keep_index: bool = False) -> subprocess.Popen[bytes]:
    """`git -C root …` as a process to stream to and from, such as `cat-file --batch`."""
    return subprocess.Popen(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=environment(keep_index=keep_index),
    )
