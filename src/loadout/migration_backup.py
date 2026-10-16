"""The before-image of every path an adoption touches, kept so it can be put back (D-06).

`init` writes it before its first change and clears the in-progress marker once it is done.
A failure restores it straight away; a run killed part way leaves the marker, and the next
`init` offers the restore. The latest completed backup stays, so an adoption can be undone.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .deployment import atomic_install
from .errors import LoadoutError
from .migration_git import git
from .output import FrozenFile
from .output import no_symlinks as _no_symlinks

BACKUP_VERSION = 2
MANIFEST = "manifest.json"
MARKER = "in-progress"


@dataclass(frozen=True)
class Image:
    kind: str = "absent"
    content: bytes = field(default=b"", repr=False)
    mode: int = 0

    def digest(self) -> str:
        return hashlib.sha256(f"{self.kind}:{self.mode:o}:".encode() + self.content).hexdigest()

    def shape(self) -> str:
        """What a planned write fixes ahead of time: the kind and the content, not the
        mode, which a co-owned file keeps from whatever is already there."""
        return f"{self.kind}:{hashlib.sha256(self.content).hexdigest()}"


@dataclass(frozen=True)
class Operation:
    path: Path
    before: Image
    after: Image
    phase: str


def snapshot(path: Path) -> Image:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return Image()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return Image("symlink", os.fsencode(os.readlink(path)), mode)
    if stat.S_ISDIR(metadata.st_mode):
        return Image("directory", mode=mode)
    if stat.S_ISREG(metadata.st_mode):
        return Image("file", path.read_bytes(), mode)
    raise LoadoutError(f"unsupported migration entry: {path}")


def install(path: Path, image: Image, boundary: Path | None = None) -> None:
    """Make `path` hold `image`. A file replaces a file atomically, never by unlinking it
    first; links are refused between `path` and `boundary` (or `/`)."""
    _no_symlinks(path.parent, boundary)
    current = snapshot(path)
    if current.kind == "directory" and image.kind != "directory":
        path.rmdir()
    elif current.kind == "symlink" or (current.kind == "file" and image.kind != "file"):
        path.unlink()
    if image.kind == "file":
        atomic_install(path, FrozenFile(image.content, image.mode), boundary)
    elif image.kind == "directory":
        path.mkdir(mode=image.mode, exist_ok=True)
        path.chmod(image.mode)
    elif image.kind == "symlink":
        path.symlink_to(os.fsdecode(image.content))


def backups_root(env: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    state = environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(environ.get("HOME", "~")).expanduser() / ".local/state"
    return (base / "loadout" / "backups").resolve()


@dataclass(frozen=True)
class Backup:
    directory: Path

    @property
    def interrupted(self) -> bool:
        return (self.directory / MARKER).exists()

    def manifest(self) -> dict[str, Any]:
        try:
            document = json.loads((self.directory / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise LoadoutError(f"unreadable backup manifest: {self.directory}: {error}") from None
        if not isinstance(document, dict) or document.get("version") != BACKUP_VERSION:
            raise LoadoutError(
                f"backup {self.directory} was written by another version of loadout and "
                "cannot be restored; delete that directory"
            )
        return document

    def images(self) -> dict[Path, Image]:
        return {
            Path(entry["path"]): Image(
                entry["kind"], base64.b64decode(entry["content"]), entry["mode"]
            )
            for entry in self.manifest()["entries"]
        }


def _write_manifest(backup: Backup, document: Mapping[str, Any]) -> None:
    atomic_install(
        backup.directory / MANIFEST,
        FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600),
    )


def _entry(path: Path, image: Image) -> dict[str, Any]:
    return {
        "path": str(path),
        "kind": image.kind,
        "mode": image.mode,
        "content": base64.b64encode(image.content).decode(),
    }


def _covering(paths: Iterable[Path]) -> tuple[Path, ...]:
    """Each path plus every ancestor a write could create: the missing ones up to the
    first that exists, which a restore must remove again."""
    covered: set[Path] = set()
    for path in paths:
        covered.add(path)
        for parent in path.parents:
            if parent.exists() or parent.is_symlink():
                break
            covered.add(parent)
    return tuple(sorted(covered))


@dataclass(frozen=True)
class Declared:
    """What init declares about its writes before making them.

    `planned` is each path's possible shapes. `records` are loadout's write records:
    restore drops the entries it reverts from them rather than putting back their
    before-image. `untracked` is the index init will change, as (index, repository,
    names), and `repository` the one it will create; both are declared before Git runs,
    so a run killed straight after still knows them.
    """

    planned: Mapping[Path, Iterable[str]]
    records: tuple[Path, ...] = ()
    untracked: tuple[Path, Path, tuple[str, ...]] | None = None
    repository: Path | None = None


def create_backup(
    paths: Iterable[Path], declared: Declared, *, now: datetime, label: Mapping[str, object]
) -> Backup:
    """Record each path's before-image beside what init declared, so an interrupted
    restore can tell init's own writes from anything else's."""
    covered = _covering(paths)
    captured = frozenset(covered)
    # Beneath a link init replaces, the path is the link target's entry, not ours.
    entries = [
        _entry(path, Image() if _beneath_link(path, captured) else _readable(path))
        for path in covered
    ]
    document = {
        "version": BACKUP_VERSION,
        **label,
        "entries": entries,
        "planned": {
            str(path): sorted(set(shapes))
            for path, shapes in declared.planned.items()
            if path in captured
        },
        "records": sorted(str(path) for path in declared.records if path in captured),
        "untracked": (
            {
                "index": str(declared.untracked[0]),
                "repository": str(declared.untracked[1]),
                "names": list(declared.untracked[2]),
            }
            if declared.untracked is not None
            else None
        ),
        "repository": str(declared.repository) if declared.repository is not None else None,
        "after": {},
    }
    root = backups_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Backup(root / now.strftime("%Y%m%dT%H%M%S%fZ"))
    try:
        backup.directory.mkdir(mode=0o700)
        _write_manifest(backup, document)
        # Last, so a marker always has a complete manifest beside it.
        (backup.directory / MARKER).write_text("")
    except BaseException:
        shutil.rmtree(backup.directory, ignore_errors=True)
        raise
    return backup


def _readable(path: Path) -> Image:
    try:
        return snapshot(path)
    except OSError as error:
        raise LoadoutError(f"cannot back up {path} before changing it: {error}") from None


def record(backup: Backup, paths: Iterable[Path], *, created: bool = False) -> None:
    """Record what init has now left at `paths`. `created` adds paths init made that
    were not planned, such as the files `git init` writes, as absent beforehand."""
    document = backup.manifest()
    known = {entry["path"] for entry in document["entries"]}
    for path in paths:
        if str(path) not in known:
            if not created:
                continue
            document["entries"].append(_entry(path, Image()))
        document["after"][str(path)] = snapshot_digest(path)
    _write_manifest(backup, document)


def complete(backup: Backup) -> None:
    """Record what the adoption left at each path, clear the marker and keep only this
    backup, so a later restore can tell an untouched path from one edited since."""
    record(backup, (Path(entry["path"]) for entry in backup.manifest()["entries"]))
    (backup.directory / MARKER).unlink(missing_ok=True)
    for other in backups():
        if other.directory != backup.directory and not other.interrupted:
            shutil.rmtree(other.directory)


def backups() -> tuple[Backup, ...]:
    root = backups_root()
    if not root.is_dir():
        return ()
    return tuple(Backup(directory) for directory in sorted(root.iterdir()) if directory.is_dir())


def interrupted_backup() -> Backup | None:
    return next((b for b in reversed(backups()) if b.interrupted and _complete(b)), None)


def _complete(backup: Backup) -> bool:
    try:
        backup.manifest()
    except LoadoutError:
        return False
    return True


def latest_backup() -> Backup | None:
    found = backups()
    return found[-1] if found else None


def _beneath_link(path: Path, captured: frozenset[Path]) -> bool:
    """Whether a captured ancestor is a symlink now, so `path` is an entry of its target."""
    return any(parent in captured and parent.is_symlink() for parent in path.parents)


@dataclass(frozen=True)
class _Plan:
    """What restoring one backup may touch, decided before it touches anything."""

    images: Mapping[Path, Image]
    planned: Mapping[str, list[str]]
    after: Mapping[str, str]
    untracked: Mapping[str, Any] | None = None
    repository: Path | None = None

    def ours(self, path: Path) -> bool:
        """Whether `path` still holds its before-image or what init planned or left."""
        current = _current(path)
        if current is None:
            return False
        created_directory = self.images[path].kind == "absent" and current.kind == "directory"
        return (
            current.digest() in {self.images[path].digest(), self.after.get(str(path))}
            or current.shape() in self.planned.get(str(path), ())
            # Only ever removed once empty, so a directory init made holds nothing at risk.
            or created_directory
            or self._untracked_only(path)
        )

    def _untracked_only(self, path: Path) -> bool:
        """The index differs from its before-image by exactly the entries init removed."""
        if self.untracked is None or str(path) != self.untracked["index"]:
            return False
        repository = Path(self.untracked["repository"])
        with tempfile.TemporaryDirectory() as directory:
            before = Path(directory) / "index"
            before.write_bytes(self.images[path].content)
            listed = _staged(repository, before)
        removed = set(self.untracked["names"])
        expected = [line for line in listed if line.split(b"\t", 1)[-1].decode() not in removed]
        return _staged(repository, path) == expected

    def disposable(self) -> frozenset[Path]:
        """The repository init was creating, while it holds nothing but what `git init`
        writes: no commit and no index. A run killed straight after `git init` recorded
        none of it, so this is how its files are known to be init's."""
        if self.repository is None:
            return frozenset()
        git_dir = self.repository / ".git"
        if not git_dir.is_dir() or git_dir.is_symlink() or (git_dir / "index").exists():
            return frozenset()
        head = git(self.repository, "rev-parse", "--verify", "-q", "HEAD", check=False)
        return frozenset({git_dir}) if head.returncode else frozenset()

    def frozen(self) -> dict[Path, tuple[Path, ...]]:
        """Each unit that cannot come back whole, with the entries in the way: a link init
        replaced with a directory, which needs that directory empty, and a repository init
        created, which removing its own files from would break. Taking either apart
        around an entry that is not init's leaves neither state, so the subtree stays."""
        result: dict[Path, tuple[Path, ...]] = {}
        for path, image in sorted(self.images.items(), key=lambda item: len(item[0].parts)):
            unit = image.kind == "symlink" or (image.kind == "absent" and path.name == ".git")
            if not unit or not path.is_dir() or path.is_symlink():
                continue
            if any(parent in result for parent in path.parents):
                continue
            foreign = tuple(
                entry
                for entry in _subtree(path)
                if entry not in self.images or not self.ours(entry)
            )
            if foreign:
                result[path] = foreign
        return result


def _staged(repository: Path, index: Path) -> list[bytes]:
    listing = git(repository, "ls-files", "-s", "-z", index=index, check=False)
    return [line for line in listing.stdout.split(b"\0") if line]


def _current(path: Path) -> Image | None:
    try:
        return snapshot(path)
    except LoadoutError:
        return None


def _subtree(path: Path) -> tuple[Path, ...]:
    found: list[Path] = []
    for directory, directories, files in os.walk(path):
        here = Path(directory)
        found.extend(here / name for name in (*directories, *files))
    return tuple(found)


def _boundary(path: Path, captured: frozenset[Path]) -> Path | None:
    """The nearest captured ancestor: links above it are the user's layout, not init's."""
    return next((parent for parent in path.parents if parent in captured), None)


def _reachable(path: Path) -> bool:
    """Whether `path` can be reached without passing a link. Every backup path is
    recorded with its ancestors resolved, so a link above it was put there since, and
    writing or deleting through it would touch somewhere else."""
    return not any(parent.is_symlink() for parent in path.parents)


@dataclass(frozen=True)
class Restored:
    """What a restore left as it was, and the links among those it could not put back."""

    kept: tuple[Path, ...]
    links: Mapping[Path, str]


def restore(backup: Backup) -> Restored:
    """Put back what init changed, and report what stays as it is.

    Only a path still holding its before-image or what init planned or left is
    reverted; anything else is someone else's and stays, reported. A directory init
    created goes only once empty. Everything is checked before anything is removed.
    Afterwards the backup is spent: deleted, or cut down to what was kept, so the undo
    can be finished once those paths are dealt with and can never replay over them.
    """
    document = backup.manifest()
    repository = document.get("repository")
    plan = _Plan(
        backup.images(),
        document.get("planned", {}),
        document.get("after", {}),
        document.get("untracked"),
        Path(repository) if repository else None,
    )
    records = {Path(path) for path in document.get("records", ())}
    disposable = plan.disposable()
    frozen = {
        unit: entries
        for unit, entries in plan.frozen().items()
        if not any(unit.is_relative_to(gone) for gone in disposable)
    }
    restorable, kept = _restorable(plan, frozen, disposable, records)
    reverted: list[Path] = []
    for unit in disposable:
        shutil.rmtree(unit)
        reverted.append(unit)
    _remove_created(plan, restorable, reverted, kept)
    _put_back(plan, restorable, reverted, kept)
    # Back at its before-image, whether restore put it there or init left it so.
    settled = [*reverted, *(path for path, image in restorable.items() if _current(path) == image)]
    for record_path in records:
        _forget(record_path, plan.images[record_path], settled)
    kept = _prune_created(restorable, kept)
    result = Restored(
        tuple(sorted(set(kept))),
        {
            path: os.fsdecode(plan.images[path].content)
            for path in frozen
            if plan.images[path].kind == "symlink"
        },
    )
    _spend(backup, document, result.kept)
    return result


def _restorable(
    plan: _Plan,
    frozen: Mapping[Path, tuple[Path, ...]],
    disposable: frozenset[Path],
    records: set[Path],
) -> tuple[dict[Path, Image], list[Path]]:
    """The paths restore may touch, and those it already knows it must keep."""
    captured = frozenset(plan.images)
    held = {entry for unit in frozen for entry in (unit, *_subtree(unit))}
    kept: list[Path] = [*frozen, *(entry for entries in frozen.values() for entry in entries)]
    restorable: dict[Path, Image] = {}
    for path, image in plan.images.items():
        skipped = path in held or path in records or _beneath_link(path, captured)
        if skipped or any(path.is_relative_to(unit) for unit in disposable):
            continue
        if _reachable(path):
            restorable[path] = image
        elif _current(path) != image:
            kept.append(path)
    return restorable, kept


def _remove_created(
    plan: _Plan, restorable: Mapping[Path, Image], reverted: list[Path], kept: list[Path]
) -> None:
    for path in sorted(restorable, key=lambda p: len(p.parts), reverse=True):
        if restorable[path].kind != "absent" or _current(path) == Image():
            continue
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
                reverted.append(path)
            except OSError:
                kept.append(path)
        elif plan.ours(path):
            path.unlink()
            reverted.append(path)
        else:
            kept.append(path)


def _put_back(
    plan: _Plan, restorable: Mapping[Path, Image], reverted: list[Path], kept: list[Path]
) -> None:
    captured = frozenset(plan.images)
    for path in sorted(restorable, key=lambda p: len(p.parts)):
        image = restorable[path]
        if image.kind == "absent" or _current(path) == image:
            continue
        replaced_link = image.kind == "symlink" and path.is_dir() and not path.is_symlink()
        if not plan.ours(path) and not replaced_link:
            kept.append(path)
            continue
        try:
            install(path, image, _boundary(path, captured))
            reverted.append(path)
        except (OSError, LoadoutError):
            kept.append(path)


def _prune_created(restorable: Mapping[Path, Image], kept: list[Path]) -> list[Path]:
    """Remove a directory init created that the write record held until now, and drop it
    from what was kept once it is gone."""
    for path in sorted(restorable, key=lambda p: len(p.parts), reverse=True):
        if restorable[path].kind == "absent" and path.is_dir() and not path.is_symlink():
            with contextlib.suppress(OSError):
                path.rmdir()
    return [path for path in kept if path.exists() or path.is_symlink()]


def _forget(record_path: Path, before: Image, reverted: list[Path]) -> None:
    """Drop the reverted destinations from a write record, keeping everything else it
    says, so a file init wrote that stays is still known as loadout's."""
    if not record_path.is_file():
        return
    gone = {entry_path_text(path) for path in reverted}
    document = json.loads(record_path.read_text(encoding="utf-8"))
    written = document.get("written", {})
    document["written"] = {
        key: value for key, value in written.items() if entry_path_text(Path(key)) not in gone
    }
    if not document["written"] and before.kind == "absent":
        record_path.unlink()
        return
    atomic_install(record_path, FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600))


def entry_path_text(path: Path) -> str:
    return str(path.parent.resolve() / path.name)


def _spend(backup: Backup, document: Mapping[str, Any], kept: tuple[Path, ...]) -> None:
    if not kept:
        shutil.rmtree(backup.directory)
        return
    remaining = [
        entry
        for entry in document["entries"]
        if any(
            Path(entry["path"]) == path or Path(entry["path"]).is_relative_to(path) for path in kept
        )
    ]
    _write_manifest(backup, {**document, "entries": remaining})
    (backup.directory / MARKER).unlink(missing_ok=True)


def snapshot_digest(path: Path) -> str:
    image = _current(path)
    return image.digest() if image is not None else ""
