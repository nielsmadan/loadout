"""The before-image of every path an adoption touches, kept so it can be put back (ADR 0028).

`init` writes it before its first change and clears the in-progress marker once it is done.
A failure restores it straight away; a run killed part way leaves the marker, and the next
`init` offers the restore. The latest completed backup stays, so an adoption can be undone.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .atomic import atomic_install, temporary_name
from .containment import first_link
from .errors import LoadoutError
from .machine import state_dir
from .migration_git import git
from .migration_images import Image, install, snapshot
from .output import FrozenFile, sha256
from .written import forget_written

BACKUP_VERSION = 4
MANIFEST = "manifest.json"
MARKER = "in-progress"
# Written once init starts changing things: a backup without it never applied anything.
APPLIED = "applied"
# Beside MARKER while a restore runs, so a killed restore is offered as one, not as an init.
RESTORING = "restoring"
IMAGES = "images"


def backups_root(env: Mapping[str, str] | None = None) -> Path:
    return state_dir(env) / "backups"


@dataclass(frozen=True)
class Backup:
    directory: Path

    @property
    def interrupted(self) -> bool:
        return (self.directory / MARKER).exists()

    @property
    def restoring(self) -> bool:
        """Whether the interruption was a restore's rather than the init's."""
        return (self.directory / RESTORING).exists()

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
            Path(entry["path"]): Image(entry["kind"], self._content(entry["sha256"]), entry["mode"])
            for entry in self.manifest()["entries"]
        }

    def _content(self, digest: str) -> bytes:
        if digest == EMPTY:
            return b""
        path = self.directory / IMAGES / digest
        try:
            content = path.read_bytes()
        except OSError:
            content = None
        if content is None or sha256(content) != digest:
            raise LoadoutError(f"backup image {path} is damaged or missing; nothing was restored")
        return content


def _write_manifest(backup: Backup, document: Mapping[str, Any]) -> None:
    atomic_install(
        backup.directory / MANIFEST,
        FrozenFile(json.dumps(document, indent=2).encode() + b"\n", 0o600),
    )


EMPTY = sha256(b"")


def _entry(path: Path, image: Image, digest: str) -> dict[str, Any]:
    return {"path": str(path), "kind": image.kind, "mode": image.mode, "sha256": digest}


def _store_images(backup: Backup, contents: Mapping[str, bytes]) -> None:
    """One file per distinct content, named by its digest."""
    directory = backup.directory / IMAGES
    directory.mkdir(mode=0o700)
    for digest, content in contents.items():
        atomic_install(directory / digest, FrozenFile(content, 0o600))


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
class Untracked:
    """The index init will change: the entries it removes from `repository`'s index."""

    index: Path
    repository: Path
    names: tuple[str, ...]


@dataclass(frozen=True)
class Declared:
    """What init declares about its writes before making them.

    `planned` is each path's possible shapes. `records` are loadout's write records:
    restore drops the entries it reverts from them rather than putting back their
    before-image. `untracked` is the index init will change, and `repository` the one
    it will create; both are declared before Git runs, so a run killed straight after
    still knows them.
    """

    planned: Mapping[Path, Iterable[str]]
    records: tuple[Path, ...] = ()
    untracked: Untracked | None = None
    repository: Path | None = None


def create_backup(
    paths: Iterable[Path], declared: Declared, *, now: datetime, label: Mapping[str, object]
) -> Backup:
    """Record each path's before-image beside what init declared, so an interrupted
    restore can tell init's own writes from anything else's."""
    covered = _covering(paths)
    captured = frozenset(covered)
    # Beneath a link init replaces, the path is the link target's entry, not ours.
    images = {
        path: Image() if _beneath_link(path, captured) else _readable(path) for path in covered
    }
    digests = {path: sha256(image.content) for path, image in images.items()}
    entries = [_entry(path, image, digests[path]) for path, image in images.items()]
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
                "index": str(declared.untracked.index),
                "repository": str(declared.untracked.repository),
                "names": list(declared.untracked.names),
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
        _store_images(
            backup,
            {digests[path]: image.content for path, image in images.items() if image.content},
        )
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
            document["entries"].append(_entry(path, Image(), EMPTY))
        document["after"][str(path)] = snapshot_digest(path)
    _write_manifest(backup, document)


def expect(backup: Backup, images: Mapping[Path, Image]) -> None:
    """Record paths init is about to create as absent beforehand and left as `images`,
    before it creates them, so a kill straight after still knows them as init's."""
    document = backup.manifest()
    known = {entry["path"] for entry in document["entries"]}
    for path, image in images.items():
        if str(path) not in known:
            document["entries"].append(_entry(path, Image(), EMPTY))
        document["after"][str(path)] = image.digest()
    _write_manifest(backup, document)


def fresh_repository(git_dir: Path) -> dict[Path, Image]:
    """What `git init` writes into `git_dir`, from a run of it in scratch."""
    with tempfile.TemporaryDirectory(prefix="loadout-git-init-") as scratch:
        git(Path(scratch), "init", "-q")
        made = Path(scratch) / ".git"
        return {
            git_dir / path.relative_to(made): snapshot(path)
            for path in (made, *sorted(made.rglob("*")))
        }


def mark_applied(backup: Backup) -> None:
    """Mark that init is about to change what the backup holds before-images of."""
    (backup.directory / APPLIED).write_text("")


def complete(backup: Backup) -> None:
    """Record what the adoption left at each path, clear the marker and keep only this
    backup, so a later restore can tell an untouched path from one edited since. An
    interrupted backup survives only if it applied something and can be read."""
    record(backup, (Path(entry["path"]) for entry in backup.manifest()["entries"]))
    (backup.directory / MARKER).unlink(missing_ok=True)
    for other in backups():
        if other.directory != backup.directory and not (other.interrupted and _usable(other)):
            shutil.rmtree(other.directory)


def backups() -> tuple[Backup, ...]:
    root = backups_root()
    if not root.is_dir():
        return ()
    return tuple(Backup(directory) for directory in sorted(root.iterdir()) if directory.is_dir())


def interrupted_backup() -> Backup | None:
    return next((b for b in reversed(backups()) if b.interrupted and _usable(b)), None)


def _usable(backup: Backup) -> bool:
    """A backup worth restoring: its manifest reads, and its run changed something. A
    run killed before either leaves a directory that would hide the real backup. One
    from another version stays, so restoring it is refused by name."""
    try:
        document = json.loads((backup.directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(document, dict):
        return False
    return document.get("version") != BACKUP_VERSION or (backup.directory / APPLIED).exists()


def latest_backup() -> Backup | None:
    return next((b for b in reversed(backups()) if _usable(b)), None)


def _beneath_link(path: Path, captured: frozenset[Path]) -> bool:
    """Whether a captured ancestor is a symlink now, so `path` is an entry of its target."""
    return any(parent in captured and parent.is_symlink() for parent in path.parents)


@dataclass(frozen=True)
class _Plan:
    """What restoring one backup may touch, decided before it touches anything."""

    images: Mapping[Path, Image]
    planned: Mapping[str, list[str]]
    after: Mapping[str, str]
    untracked: Untracked | None = None
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
        if self.untracked is None or path != self.untracked.index:
            return False
        repository = self.untracked.repository
        with tempfile.TemporaryDirectory() as directory:
            before = Path(directory) / "index"
            before.write_bytes(self.images[path].content)
            listed = _staged(repository, before)
        removed = set(self.untracked.names)
        expected = [line for line in listed if line.split(b"\t", 1)[-1].decode() not in removed]
        return _staged(repository, path) == expected

    def disposable(self) -> frozenset[Path]:
        """The repository init created, while it is provably init's: recorded when `git
        init` wrote it, reached without a link, and every entry under it still as init
        left it. A hook, a remote or a commit added since makes it the user's."""
        git_dir = self._new_git_dir()
        if git_dir is None or not _reachable(git_dir):
            return frozenset()
        unborn = _unborn(git_dir)
        entries = (git_dir, *_subtree(git_dir))
        if all(
            (entry in self.images and self.ours(entry)) or (unborn and _runtime(git_dir, entry))
            for entry in entries
        ):
            return frozenset({git_dir})
        return frozenset()

    def _new_git_dir(self) -> Path | None:
        """The git dir of the repository init created, while it is still a directory."""
        if self.repository is None:
            return None
        git_dir = self.repository / ".git"
        return git_dir if git_dir.is_dir() and not git_dir.is_symlink() else None

    def frozen(self) -> dict[Path, tuple[Path, ...]]:
        """Each unit that cannot come back whole, with the entries in the way: a link init
        replaced with a directory, which needs that directory empty, and a repository init
        created, which removing its own files from would break. Taking either apart
        around an entry that is not init's leaves neither state, so the subtree stays."""
        result: dict[Path, tuple[Path, ...]] = {}
        for path in _by_depth(self.images):
            image = self.images[path]
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


def _unborn(git_dir: Path) -> bool:
    """No index, no ref and no packed ref: nothing in the repository is the user's yet."""
    if (git_dir / "index").exists():
        return False
    if any(path.is_file() for path in (git_dir / "refs").rglob("*")):
        return False
    packed = git_dir / "packed-refs"
    if not packed.is_file():
        return True
    lines = packed.read_text(encoding="utf-8", errors="replace").splitlines()
    return all(not line.strip() or line.startswith(("#", "^")) for line in lines)


def _runtime(git_dir: Path, entry: Path) -> bool:
    """What read-only git writes on its own: fsmonitor's daemon state, a fetch, gc's
    packing and locks. A hook, a remote or a config edit is none of these."""
    relative = entry.relative_to(git_dir)
    return (
        relative.parts[0].startswith("fsmonitor--daemon")
        or relative.as_posix() in {"FETCH_HEAD", "packed-refs", "info/refs"}
        or relative.parent.is_relative_to("objects/info")
        or entry.name.endswith(".lock")
    )


def _staged(repository: Path, index: Path) -> list[bytes]:
    listing = git(repository, "ls-files", "-s", "-z", index=index, check=False)
    return [line for line in listing.stdout.split(b"\0") if line]


def _current(path: Path) -> Image | None:
    try:
        return snapshot(path)
    except LoadoutError:
        return None


def _by_depth(paths: Iterable[Path], *, deepest_first: bool = False) -> list[Path]:
    return sorted(paths, key=lambda p: len(p.parts), reverse=deepest_first)


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
    return first_link(Path(path.anchor), path.parent) is None


@dataclass(frozen=True)
class Restored:
    """What a restore put back, what it left as it was and why, and the links among those
    it could not put back."""

    kept: tuple[Path, ...]
    links: Mapping[Path, str]
    reasons: Mapping[Path, str] = field(default_factory=dict)
    restored: tuple[Path, ...] = ()


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
    untracked = document.get("untracked")
    plan = _Plan(
        backup.images(),
        document.get("planned", {}),
        document.get("after", {}),
        Untracked(
            Path(untracked["index"]), Path(untracked["repository"]), tuple(untracked["names"])
        )
        if untracked
        else None,
        Path(repository) if repository else None,
    )
    records = {Path(path) for path in document.get("records", ())}
    missing = sorted(str(path) for path in records if path not in plan.images)
    if missing:
        raise LoadoutError(
            f"backup {backup.directory}: write record {', '.join(missing)} has no before-image, "
            "so the undo cannot finish; nothing was restored"
        )
    # From here restore changes things, so a kill leaves the backup offered again.
    (backup.directory / MARKER).write_text("")
    (backup.directory / RESTORING).write_text("")
    disposable = plan.disposable()
    frozen = {
        unit: entries
        for unit, entries in plan.frozen().items()
        if not any(unit.is_relative_to(gone) for gone in disposable)
    }
    restorable, kept = _restorable(plan, frozen, disposable, records)
    for unit in disposable:
        shutil.rmtree(unit)
    # A write killed at its rename leaves its temporary file beside the path, in a folder
    # that may have been there before init (review 4, batch 2.6); the write record is
    # restored apart from the rest, so its folder is named on its own.
    beside = {*restorable, *(record for record in records if _reachable(record))}
    for folder in sorted({path.parent for path in beside}):
        _drop_interrupted_writes(folder)
    removed, unremoved = _remove_created(plan, restorable, records)
    put_back, not_put_back = _put_back(plan, restorable)
    reverted = [*disposable, *removed, *put_back]
    kept = {**unremoved, **not_put_back, **kept}
    # Back at its before-image, whether restore put it there or init left it so.
    settled = [*reverted, *(path for path, image in restorable.items() if _current(path) == image)]
    for record_path in records:
        # A link added above it since init would have restore rewrite its target.
        if not _reachable(record_path):
            kept[record_path] = "now reached through a link added since init"
            continue
        forget_written(record_path, settled, created=plan.images[record_path].kind == "absent")
    _remove_emptied(restorable)
    kept = _still_kept(plan.images, kept)
    result = Restored(
        tuple(sorted(kept)),
        {
            path: os.fsdecode(plan.images[path].content)
            for path in frozen
            if plan.images[path].kind == "symlink"
        },
        dict(sorted(kept.items())),
        tuple(sorted(reverted)),
    )
    _spend(backup, document, result.kept)
    return result


CHANGED = "changed since init"


def _unit_reason(image: Image) -> str:
    if image.kind == "symlink":
        return "a link init replaced, kept whole because it now holds entries that are not init's"
    return "a repository init created, kept whole because it now holds files init did not write"


def _restorable(
    plan: _Plan,
    frozen: Mapping[Path, tuple[Path, ...]],
    disposable: frozenset[Path],
    records: set[Path],
) -> tuple[dict[Path, Image], dict[Path, str]]:
    """The paths restore may touch, and those it already knows it must keep, with why."""
    captured = frozenset(plan.images)
    held = {entry for unit in frozen for entry in (unit, *_subtree(unit))}
    kept: dict[Path, str] = {unit: _unit_reason(plan.images[unit]) for unit in frozen}
    kept.update(
        (entry, CHANGED if entry in plan.images else "added since init")
        for entries in frozen.values()
        for entry in entries
    )
    restorable: dict[Path, Image] = {}
    for path, image in plan.images.items():
        skipped = path in held or path in records or _beneath_link(path, captured)
        if skipped or any(path.is_relative_to(unit) for unit in disposable):
            continue
        if _reachable(path):
            restorable[path] = image
        elif _current(path) != image:
            kept.setdefault(path, "now reached through a link added since init")
    return restorable, kept


def _remove_created(
    plan: _Plan, restorable: Mapping[Path, Image], records: set[Path]
) -> tuple[list[Path], dict[Path, str]]:
    """Remove what init created, deepest first: what went, and what stays with why."""
    removed: list[Path] = []
    kept: dict[Path, str] = {}
    for path in _by_depth(restorable, deepest_first=True):
        if restorable[path].kind != "absent" or _current(path) == Image():
            continue
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
                removed.append(path)
            except OSError:
                kept[path] = _blocked(path, records, kept.keys())
        elif plan.ours(path):
            path.unlink()
            removed.append(path)
        else:
            kept[path] = CHANGED
    return removed, kept


def _drop_interrupted_writes(folder: Path) -> None:
    """Remove each write of loadout's a kill cut short in `folder`, which holds a path in
    the backup: a regular file named exactly as `atomic` names a write in progress. Any
    other file, `.loadout-notes.md` or a skill's `.loadout-bundle.toml` among them, is
    someone else's and stays (review 5, batch 1.1)."""
    with contextlib.suppress(OSError):
        for entry in folder.iterdir():
            if temporary_name(entry.name) and entry.is_file() and not entry.is_symlink():
                entry.unlink()


BLOCKERS_SHOWN = 3


def _blocked(folder: Path, records: set[Path], kept: Iterable[Path]) -> str:
    """Why a folder init created stays, naming what is in the way: never a path restore
    itself keeps there, which is reported on its own (review 4, batch 2.6)."""
    # The write record is dealt with after this, so it is no reason on its own.
    held = {*records, *kept}
    entries = sorted(folder.iterdir())
    others = [entry for entry in entries if entry not in held]
    if not others:
        if all(entry in records for entry in entries):
            return "a folder init created, kept for the write record restore kept in it"
        return "a folder init created, kept for what restore kept inside it"
    names = [entry.name for entry in others[:BLOCKERS_SHOWN]]
    more = len(others) - len(names)
    listed = ", ".join(names) + (f" and {more} more" if more else "")
    return f"a folder init created that now holds files it did not ({listed})"


def _put_back(plan: _Plan, restorable: Mapping[Path, Image]) -> tuple[list[Path], dict[Path, str]]:
    """Install each before-image init replaced, shallowest first: what was put back, and
    what stays with why."""
    captured = frozenset(plan.images)
    put_back: list[Path] = []
    kept: dict[Path, str] = {}
    for path in _by_depth(restorable):
        image = restorable[path]
        if image.kind == "absent" or _current(path) == image:
            continue
        replaced_link = image.kind == "symlink" and path.is_dir() and not path.is_symlink()
        # Gone since init: putting the original back overwrites nothing (review 3, 1.12).
        cleared = _current(path) == Image()
        if not plan.ours(path) and not replaced_link and not cleared:
            kept[path] = CHANGED
            continue
        try:
            install(path, image, _boundary(path, captured))
            put_back.append(path)
        except (OSError, LoadoutError) as error:
            kept[path] = f"could not be put back: {error}"
    return put_back, kept


def _remove_emptied(restorable: Mapping[Path, Image]) -> None:
    """Remove each directory init created that the write record held until now."""
    for path in _by_depth(restorable, deepest_first=True):
        if restorable[path].kind == "absent" and path.is_dir() and not path.is_symlink():
            with contextlib.suppress(OSError):
                path.rmdir()


def _still_kept(images: Mapping[Path, Image], kept: Mapping[Path, str]) -> dict[Path, str]:
    """What stays kept: a path still there, or one that held something before init, so its
    before-image is never spent while absent."""
    return {
        path: why
        for path, why in kept.items()
        if path.exists() or path.is_symlink() or images.get(path, Image()).kind != "absent"
    }


def _spend(backup: Backup, document: Mapping[str, Any], kept: tuple[Path, ...]) -> None:
    if not kept:
        shutil.rmtree(backup.directory)
        return
    # What the next restore reads: the kept paths, and the write records it updates,
    # which sit in a directory that need not be among them.
    records = {Path(path) for path in document.get("records", ())}
    remaining = [
        entry
        for entry in document["entries"]
        if Path(entry["path"]) in records
        or any(
            Path(entry["path"]) == path or Path(entry["path"]).is_relative_to(path) for path in kept
        )
    ]
    _write_manifest(backup, {**document, "entries": remaining})
    (backup.directory / MARKER).unlink(missing_ok=True)
    (backup.directory / RESTORING).unlink(missing_ok=True)
    needed = {entry["sha256"] for entry in remaining}
    for image in (backup.directory / IMAGES).iterdir():
        if image.name not in needed:
            image.unlink()


def snapshot_digest(path: Path) -> str:
    image = _current(path)
    return image.digest() if image is not None else ""
