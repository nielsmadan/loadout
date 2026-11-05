from __future__ import annotations

import functools
import glob
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from . import containment, overlay_git
from .destinations import resolve_destination
from .errors import LoadoutError
from .overlay_manifest import OverlaySettings

SkipKind = Literal[
    "submodule",
    "linked-worktree",
    "not-a-checkout",
    "adopted",
    "unexamined",
    "git-failed",
    "unsafe",
]
RecordedFate = Literal["present", "gone", "unreachable"]

_HOME_TEMPLATES = {
    "claude": "${CLAUDE_CONFIG_DIR:-~/.claude}",
    "codex": "${CODEX_HOME:-~/.codex}",
    "droid": "${FACTORY_HOME_OVERRIDE:-~}/.factory",
    "opencode": "${XDG_CONFIG_HOME:-~/.config}/opencode",
    "pi": "${PI_CODING_AGENT_DIR:-~/.pi/agent}",
}

_GLOB_CHARACTERS = frozenset("*?[")

_SUBMODULE = (
    "submodules are not supported as targets; the parent repository still renders "
    "when the pattern matches it"
)
_LINKED_WORKTREE = "linked worktrees are not rendered as targets"


@dataclass(frozen=True)
class Target:
    root: Path
    common_dir: Path
    device: int


@dataclass(frozen=True)
class SkippedTarget:
    folder: Path
    kind: SkipKind
    reason: str

    @property
    def failure(self) -> bool:
        return self.kind in ("git-failed", "unsafe")


@dataclass(frozen=True)
class HarnessHomes:
    home: Path
    homes: tuple[Path, ...]


@dataclass(frozen=True)
class Expansion:
    targets: tuple[Target, ...]
    skipped: tuple[SkippedTarget, ...]
    matched: frozenset[Path]
    unlisted: tuple[str, ...]
    unlisted_under: tuple[Path, ...]


def harness_homes(resolve: Callable[[str, str], Path] = resolve_destination) -> HarnessHomes:
    return HarnessHomes(
        home=resolve("~", "HOME").resolve(),
        homes=tuple(
            resolve(template, f"{harness} home").resolve()
            for harness, template in _HOME_TEMPLATES.items()
        ),
    )


def _split(pattern: str, home: Path) -> tuple[Path, str]:
    base, rest = (home, pattern[2:]) if pattern.startswith("~/") else (Path("/"), pattern)
    parts = tuple(part for part in rest.split("/") if part)
    first_glob = next(
        (index for index, part in enumerate(parts) if _GLOB_CHARACTERS & set(part)),
        max(len(parts) - 1, 0),
    )
    return base.joinpath(*parts[:first_glob]), "/".join(parts[first_glob:])


def _listable(folder: Path) -> bool:
    try:
        with os.scandir(folder):
            pass
    # A missing parent matches nothing; recorded_fate tells a deleted tree from a lost volume.
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return True


def _matches(fixed: Path, rest: str) -> tuple[Path, ...]:
    if not rest:
        return (fixed.resolve(),) if fixed.is_dir() else ()
    found = sorted(glob.glob(rest, root_dir=fixed))
    folders = (fixed / name for name in found if (fixed / name).is_dir())
    return tuple(dict.fromkeys(folder.resolve() for folder in folders))


def expand_patterns(
    patterns: Sequence[str], home: Path
) -> tuple[dict[str, tuple[Path, ...]], tuple[str, ...]]:
    split = {pattern: _split(pattern, home) for pattern in patterns}
    unlisted = tuple(pattern for pattern, (fixed, _) in split.items() if not _listable(fixed))
    return (
        {
            pattern: _matches(fixed, rest)
            for pattern, (fixed, rest) in split.items()
            if pattern not in unlisted
        },
        unlisted,
    )


def _same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left == right


def _identity_key(folder: Path) -> tuple[int, int] | Path:
    try:
        status = os.stat(folder)
    except OSError:
        return folder
    return (status.st_dev, status.st_ino)


def _unique(folders: Iterable[Path]) -> tuple[Path, ...]:
    kept: dict[tuple[int, int] | Path, Path] = {}
    for folder in folders:
        kept.setdefault(_identity_key(folder), folder)
    return tuple(kept.values())


def _skipped(folder: Path, kind: SkipKind, reason: str) -> SkippedTarget:
    return SkippedTarget(folder=folder, kind=kind, reason=reason)


def _inside(path: Path, folder: Path) -> bool:
    return any(_same(ancestor, folder) for ancestor in (path, *path.parents))


def _unsafe_reason(root: Path, homes: HarnessHomes, overlay_folder: Path) -> str | None:
    overlay = overlay_folder.resolve()
    if _same(root, homes.home):
        return "a target cannot be the home folder"
    held = next((home for home in homes.homes if _inside(home, root)), None)
    if held is not None:
        return f"a target cannot hold the harness home {held}"
    if _inside(overlay, root):
        return f"a target cannot hold the overlay folder {overlay}"
    if _inside(root, overlay):
        return f"a target cannot be inside the overlay folder {overlay}"
    return None


def _skip(
    root: Path,
    found: overlay_git.GitIdentity,
    parent: Path | None,
    homes: HarnessHomes,
    overlay_folder: Path,
) -> tuple[SkipKind, str] | None:
    if not _same(found.toplevel, root):
        if not overlay_git.has_git_entry(root):
            return "not-a-checkout", "not a git checkout"
        return "unsafe", f"inside the checkout {found.toplevel}; a target is the top of a checkout"
    if parent is not None:
        return "submodule", _SUBMODULE
    if found.git_dir != found.common_dir:
        return "linked-worktree", _LINKED_WORKTREE
    reason = _unsafe_reason(root, homes, overlay_folder)
    return ("unsafe", reason) if reason is not None else None


def probe(folder: Path, homes: HarnessHomes, overlay_folder: Path) -> Target | SkippedTarget:
    root = folder.resolve()
    try:
        found = overlay_git.identity(root)
        if found is not None and _same(found.toplevel, root):
            root = found.toplevel
        parent = (
            overlay_git.superproject(root) if found is not None and found.toplevel == root else None
        )
    except overlay_git.BareRepository:
        return _skipped(root, "not-a-checkout", "a bare repository has no working tree")
    except overlay_git.GitFailure as error:
        return _skipped(root, "git-failed", str(error))
    if found is None:
        return _skipped(root, "not-a-checkout", "not a git checkout")
    if (skip := _skip(root, found, parent, homes, overlay_folder)) is not None:
        return _skipped(root, *skip)
    return Target(
        root=root,
        common_dir=found.common_dir,
        device=os.stat(root).st_dev,
    )


def expand_targets(
    settings: OverlaySettings,
    homes: HarnessHomes,
    overlay_folder: Path,
    *,
    only: Path | None = None,
) -> Expansion:
    matches, unlisted = expand_patterns(settings.targets, homes.home)
    folders = _unique(folder for found in matches.values() for folder in found)
    matched = frozenset(folders)
    wanted = only.resolve() if only is not None else None
    probed = tuple(
        probe(folder, homes, overlay_folder)
        for folder in folders
        if wanted is None or _same(folder, wanted)
    )
    return Expansion(
        targets=tuple(result for result in probed if isinstance(result, Target)),
        skipped=tuple(result for result in probed if isinstance(result, SkippedTarget)),
        matched=matched,
        unlisted=unlisted,
        unlisted_under=tuple(_split(pattern, homes.home)[0].resolve() for pattern in unlisted),
    )


def _nearest_device(path: Path) -> int | None:
    for ancestor in path.parents:
        try:
            return os.stat(ancestor).st_dev
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return None


def recorded_fate(root: Path, device: int) -> RecordedFate:
    try:
        os.lstat(root)
    except FileNotFoundError:
        return "gone" if _nearest_device(root) == device else "unreachable"
    except OSError:
        return "unreachable"
    return "present"


def refuse_unsafe_outputs(target: Target, paths: Iterable[Path], outputs: Iterable[Path]) -> None:
    paths = tuple(paths)
    containment.refuse_escaping(target.root, paths, outputs)
    holds_git = functools.cache(_holds_git)
    problems = tuple(
        f"{path}: {repository} is a repository of its own"
        for path in paths
        if (repository := _nested_repository(target.root, path, holds_git)) is not None
    )
    if problems:
        raise LoadoutError(
            f"refusing to write {'; '.join(problems)}. An overlay writes only into the "
            "target's own checkout"
        )


def _holds_git(folder: Path) -> bool:
    return os.path.lexists(folder / ".git")


def _nested_repository(root: Path, path: Path, holds_git: Callable[[Path], bool]) -> Path | None:
    relative = containment.within(root, path, root.resolve()) or PurePosixPath(".")
    folders = (root.joinpath(*relative.parts[:depth]) for depth in range(1, len(relative.parts)))
    return next((folder for folder in folders if holds_git(folder)), None)
