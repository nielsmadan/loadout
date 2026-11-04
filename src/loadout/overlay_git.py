from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from . import git_runner
from .errors import LoadoutError

_NOT_A_REPOSITORY = b"not a git repository"
_NO_WORK_TREE = b"must be run in a work tree"
_UNSET = 1
_NOTHING_IGNORED = 1
_HEADS = "refs/heads/"
_IDENTITY_LINES = 3


class GitFailure(LoadoutError):
    pass


class BareRepository(GitFailure):
    pass


@dataclass(frozen=True)
class GitIdentity:
    toplevel: Path
    git_dir: Path
    common_dir: Path


def _detail(result: subprocess.CompletedProcess[bytes]) -> str:
    return result.stderr.decode(errors="replace").strip()


def _failure(
    root: Path, arguments: tuple[str, ...], result: subprocess.CompletedProcess[bytes]
) -> GitFailure:
    if result.returncode == git_runner.NOT_RUN:
        return GitFailure(f"{root}: {_detail(result)}")
    command = arguments[2] if arguments[0] == "-c" else arguments[0]
    return GitFailure(f"{root}: git {command} failed: {_detail(result)}")


def _checked(
    root: Path, *arguments: str, data: bytes | None = None, allowed: frozenset[int] = frozenset({0})
) -> subprocess.CompletedProcess[bytes]:
    result = git_runner.run(root, *arguments, data=data)
    if result.returncode not in allowed:
        raise _failure(root, arguments, result)
    return result


def has_git_entry(folder: Path) -> bool:
    try:
        os.lstat(folder / ".git")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def identity(folder: Path) -> GitIdentity | None:
    arguments = (
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
    )
    result = git_runner.run(folder, *arguments)
    if result.returncode == git_runner.NOT_RUN:
        raise _failure(folder, arguments, result)
    if result.returncode and _NOT_A_REPOSITORY in result.stderr:
        if not has_git_entry(folder):
            return None
        raise GitFailure(f"{folder}: git cannot read its repository: {_detail(result)}")
    if result.returncode and _NO_WORK_TREE in result.stderr:
        raise BareRepository(f"{folder}: a bare repository has no working tree")
    if result.returncode:
        raise _failure(folder, arguments, result)
    lines = os.fsdecode(result.stdout).removesuffix("\n").split("\n")
    if len(lines) != _IDENTITY_LINES:
        raise GitFailure(
            f"{folder}: git rev-parse answered {len(lines)} lines, expected {_IDENTITY_LINES}"
        )
    toplevel, git_dir, common_dir = (Path(line).resolve() for line in lines)
    return GitIdentity(toplevel=toplevel, git_dir=git_dir, common_dir=common_dir)


def superproject(folder: Path) -> Path | None:
    result = _checked(folder, "rev-parse", "--show-superproject-working-tree")
    spelled = os.fsdecode(result.stdout).strip()
    return Path(spelled).resolve() if spelled else None


def _names(output: bytes) -> tuple[str, ...]:
    return tuple(os.fsdecode(name) for name in output.split(b"\0") if name)


def tracked_units(root: Path, units: Iterable[str]) -> frozenset[str]:
    names = tuple(units)
    if not names:
        return frozenset()
    result = _checked(root, "ls-files", "-z", "--", *(f":(literal){name}" for name in names))
    listed = _names(result.stdout)
    return frozenset(
        name for name in names if any(f == name or f.startswith(f"{name}/") for f in listed)
    )


def branches_tracking(root: Path, units: Iterable[str]) -> dict[str, tuple[str, ...]]:
    names = tuple(units)
    if not names:
        return {}
    refs = _checked(root, "for-each-ref", "--format=%(refname)", _HEADS)
    branches = tuple(name for name in os.fsdecode(refs.stdout).split("\n") if name)
    if not branches:
        return dict.fromkeys(names, ())
    pathspecs = tuple(f":(literal){name}" for name in names)
    listings = {
        branch: frozenset(
            _names(_checked(root, "ls-tree", "-z", "--name-only", branch, "--", *pathspecs).stdout)
        )
        for branch in branches
    }
    return {
        name: tuple(branch.removeprefix(_HEADS) for branch in branches if name in listings[branch])
        for name in names
    }


def excludes_file(root: Path) -> Path:
    result = _checked(
        root, "config", "--path", "--get", "core.excludesFile", allowed=frozenset({0, _UNSET})
    )
    configured = os.fsdecode(result.stdout).strip()
    if configured:
        return (root / configured).resolve()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return (base / "git" / "ignore").resolve()


@contextmanager
def scratch_repo() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="loadout-overlay-") as folder:
        scratch = Path(folder)
        _checked(scratch, "init", "-q", "--template=")
        yield scratch


def _queries(units: Iterable[str], folders: frozenset[str]) -> dict[str, str]:
    return {f"{unit}/" if unit in folders else unit: unit for unit in units}


def _check_ignore(
    root: Path, queries: dict[str, str], *, config: tuple[str, ...] = (), non_matching: bool = False
) -> tuple[tuple[str, str, str, str], ...]:
    data = b"".join(os.fsencode(query) + b"\0" for query in queries)
    result = _checked(
        root,
        *config,
        "check-ignore",
        "-v",
        *(("-n",) if non_matching else ()),
        "-z",
        "--no-index",
        "--stdin",
        data=data,
        allowed=frozenset({0, _NOTHING_IGNORED}),
    )
    fields = tuple(os.fsdecode(field) for field in result.stdout.split(b"\0")[:-1])
    return tuple(
        (fields[i], fields[i + 1], fields[i + 2], fields[i + 3]) for i in range(0, len(fields), 4)
    )


def covered_by(
    scratch: Path, excludes: Path, units: Iterable[str], folders: frozenset[str]
) -> frozenset[str]:
    queries = _queries(units, folders)
    if not queries:
        return frozenset()
    wanted = excludes.resolve()
    matches = _check_ignore(scratch, queries, config=("-c", f"core.excludesFile={excludes}"))
    return frozenset(
        queries[path]
        for source, _, pattern, path in matches
        if not pattern.startswith("!") and (scratch / source).resolve() == wanted
    )


def unignored(root: Path, units: Iterable[str], folders: frozenset[str]) -> dict[str, str]:
    queries = _queries(units, folders)
    if not queries:
        return {}
    matches = _check_ignore(root, queries, non_matching=True)
    return {
        queries[path]: f"{source}:{line}:{pattern}" if pattern else ""
        for source, line, pattern, path in matches
        if not pattern or pattern.startswith("!")
    }
