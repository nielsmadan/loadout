from __future__ import annotations

import ctypes
import errno
import os
import shutil
import stat
import sys
import tempfile
import tomllib
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .bundled_skill import SKILL_NAME
from .emit import Copied, Merged, declared_profiles, render_global
from .errors import LoadoutError
from .manifest import Manifest, load_profile, resolve_destination
from .templates import copy_tree, tree_hash

OWNER_MARKER = ".loadout-bundle.toml"
_OWNER = "loadout-bundled-skill"
_AT_FDCWD = -100
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004


class SourceSkillState(StrEnum):
    MISSING = "missing"
    INSTALLED = "installed"
    UPDATE_AVAILABLE = "update available"
    MODIFIED = "modified"
    CONFLICTING = "conflicting"


@dataclass(frozen=True)
class SkillSourceLocation:
    source: str
    path: Path
    state: SourceSkillState
    recorded_hash: str | None
    bundle_hash: str


@dataclass(frozen=True)
class _ExpectedOutput:
    content: bytes
    executable: bool | None


def _load_skill_profile(root: Path, profile: str) -> Manifest:
    declared = declared_profiles(root)
    if profile not in declared:
        known = ", ".join(sorted(declared))
        raise LoadoutError(f"unknown profile {profile!r} (declared: {known})")
    return load_profile(root, profile)


def _entry_exists(path: Path) -> bool:
    return path.is_symlink() or path.exists()


def _read_marker(path: Path) -> str | None:
    marker = path / OWNER_MARKER
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        with marker.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if set(data) != {"owner", "hash"} or data.get("owner") != _OWNER:
        return None
    recorded = data.get("hash")
    return recorded if isinstance(recorded, str) and recorded.startswith("sha256:") else None


def _classify(path: Path, bundle_hash: str) -> tuple[SourceSkillState, str | None]:
    if not _entry_exists(path):
        return SourceSkillState.MISSING, None
    if path.is_symlink() or not path.is_dir():
        return SourceSkillState.CONFLICTING, None
    recorded = _read_marker(path)
    if recorded is None:
        return SourceSkillState.CONFLICTING, None
    if tree_hash(path) != recorded:
        return SourceSkillState.MODIFIED, recorded
    if recorded == bundle_hash:
        return SourceSkillState.INSTALLED, recorded
    return SourceSkillState.UPDATE_AVAILABLE, recorded


def inspect_skill_source(
    root: Path,
    profile: str,
    bundle: Path,
    source_name: str | None = None,
) -> SkillSourceLocation:
    manifest = _load_skill_profile(root, profile)
    candidates = tuple(source for source in manifest.sources if "skills" in source.use)
    if source_name is not None:
        named = tuple(source for source in candidates if source.name == source_name)
        if not named:
            available = ", ".join(source.name for source in candidates) or "none"
            raise LoadoutError(
                f"source {source_name!r} does not offer skills (available: {available})"
            )
        selected = named[0]
    else:
        existing = tuple(
            source for source in candidates if _entry_exists(source.path / "skills" / SKILL_NAME)
        )
        if len(existing) > 1:
            names = ", ".join(source.name for source in existing)
            raise LoadoutError(f"multiple sources contain skill {SKILL_NAME!r}: {names}")
        if existing:
            selected = existing[0]
        elif len(candidates) == 1:
            selected = candidates[0]
        else:
            names = ", ".join(source.name for source in candidates) or "none"
            raise LoadoutError(
                f"multiple global sources can hold skills ({names}); choose one with --source"
                if candidates
                else "no global source offers skills"
            )

    path = selected.path / "skills" / SKILL_NAME
    other = tuple(
        source.name
        for source in candidates
        if source.name != selected.name and _entry_exists(source.path / "skills" / SKILL_NAME)
    )
    if other:
        raise LoadoutError(f"skill {SKILL_NAME!r} already exists in source(s) {', '.join(other)}")
    bundle_hash = tree_hash(bundle)
    state, recorded = _classify(path, bundle_hash)
    return SkillSourceLocation(selected.name, path, state, recorded, bundle_hash)


def configured_skill_agents(root: Path, profile: str) -> tuple[str, ...]:
    manifest = _load_skill_profile(root, profile)
    return tuple(sorted({target.agent for target in manifest.skills}))


def _write_marker(path: Path, digest: str) -> None:
    (path / OWNER_MARKER).write_text(f'owner = "{_OWNER}"\nhash = "{digest}"\n', encoding="utf-8")


def _rename_without_replacing(source: Path, destination: Path) -> None:
    if os.name == "nt":
        os.rename(source, destination)
        return

    library = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        renamex_np = library.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renamex_np(source_bytes, destination_bytes, _RENAME_EXCL)
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        renameat2 = library.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        ctypes.set_errno(0)
        result = renameat2(
            _AT_FDCWD,
            source_bytes,
            _AT_FDCWD,
            destination_bytes,
            _RENAME_NOREPLACE,
        )
    else:
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP), destination)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), destination)


def _entry_identity(entry: os.stat_result) -> tuple[int, int, int]:
    return entry.st_dev, entry.st_ino, stat.S_IFMT(entry.st_mode)


def _restore(quarantined: Path, destination: Path) -> None:
    try:
        _rename_without_replacing(quarantined, destination)
    except OSError as error:
        raise LoadoutError(f"entry retained for recovery at {quarantined}") from error


def _stage_bundle(destination: Path, bundle: Path, digest: str) -> tuple[Path, Path]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".loadout-skill-install-", dir=destination.parent))
    staged = staging_root / SKILL_NAME
    copy_tree(bundle, staged)
    _write_marker(staged, digest)
    return staging_root, staged


def install_skill_source(location: SkillSourceLocation, bundle: Path) -> bool:
    if tree_hash(bundle) != location.bundle_hash:
        raise LoadoutError(f"bundled skill at {bundle} changed after it was inspected")
    state, _ = _classify(location.path, location.bundle_hash)
    if state is SourceSkillState.INSTALLED:
        return False
    if state is SourceSkillState.CONFLICTING:
        raise LoadoutError(f"{location.path} exists and is not owned by loadout")
    if state is SourceSkillState.MODIFIED:
        raise LoadoutError(f"{location.path} was modified after installation; left unchanged")

    staging_root, staged = _stage_bundle(location.path, bundle, location.bundle_hash)
    quarantine_root: Path | None = None
    quarantined: Path | None = None
    try:
        if state is SourceSkillState.MISSING:
            _rename_without_replacing(staged, location.path)
            return True

        before = location.path.lstat()
        quarantine_root = Path(
            tempfile.mkdtemp(prefix=".loadout-skill-update-", dir=location.path.parent)
        )
        quarantined = quarantine_root / SKILL_NAME
        location.path.rename(quarantined)
        moved = quarantined.lstat()
        moved_state, _ = _classify(quarantined, location.bundle_hash)
        if _entry_identity(moved) != _entry_identity(before) or moved_state is not state:
            _restore(quarantined, location.path)
            raise LoadoutError(f"{location.path} changed while the update was being applied")
        try:
            _rename_without_replacing(staged, location.path)
        except OSError:
            _restore(quarantined, location.path)
            raise
        shutil.rmtree(quarantined)
        return True
    except OSError as error:
        raise LoadoutError(f"could not install {location.path}: {error}") from error
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
        if quarantine_root is not None:
            with suppress(OSError):
                quarantine_root.rmdir()


def _skill_outputs(root: Path, profile: str) -> dict[Path, _ExpectedOutput]:
    manifest = _load_skill_profile(root, profile)
    directories = tuple(
        resolve_destination(str(destination), f"{target.agent}.skills") / SKILL_NAME
        for target in manifest.skills
        for destination in target.destinations
    )
    outputs = render_global(root, profile)
    selected: dict[Path, _ExpectedOutput] = {}
    for path, output in outputs.items():
        if any(path.is_relative_to(directory) for directory in directories):
            if isinstance(output, Copied):
                selected[path] = _ExpectedOutput(
                    output.source.read_bytes(), bool(output.source.stat().st_mode & 0o111)
                )
            elif isinstance(output, Merged):
                raise LoadoutError(f"skill output {path} unexpectedly requires a merged document")
            else:
                selected[path] = _ExpectedOutput(output.encode("utf-8"), None)
    return selected


def _matches_output(path: Path, expected: _ExpectedOutput) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.read_bytes() != expected.content:
        return False
    return expected.executable is None or bool(path.stat().st_mode & 0o111) == expected.executable


def _validate_outputs(outputs: dict[Path, _ExpectedOutput]) -> None:
    for path, expected in outputs.items():
        if not _entry_exists(path):
            continue
        if not _matches_output(path, expected):
            raise LoadoutError(f"{path} was modified outside loadout; no changes made")


def _quarantine_file(path: Path) -> tuple[Path, Path]:
    before = path.lstat()
    container = Path(tempfile.mkdtemp(prefix=".loadout-skill-remove-", dir=path.parent))
    quarantined = container / path.name
    path.rename(quarantined)
    if _entry_identity(quarantined.lstat()) != _entry_identity(before):
        _restore(quarantined, path)
        container.rmdir()
        raise LoadoutError(f"{path} changed while the uninstall was being applied")
    return quarantined, container


def _remove_empty_parents(paths: tuple[Path, ...]) -> None:
    parents: set[Path] = set()
    for path in paths:
        parent = path.parent
        while True:
            parents.add(parent)
            if parent.name == SKILL_NAME:
                break
            parent = parent.parent
    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        if parent.name == SKILL_NAME or any(
            ancestor.name == SKILL_NAME for ancestor in parent.parents
        ):
            with suppress(OSError):
                parent.rmdir()


def _remove_source_and_outputs(
    location: SkillSourceLocation,
    state: SourceSkillState,
    outputs: dict[Path, _ExpectedOutput],
) -> tuple[Path, ...]:
    source_before = location.path.lstat()
    source_container = Path(
        tempfile.mkdtemp(prefix=".loadout-skill-uninstall-", dir=location.path.parent)
    )
    quarantined_source = source_container / SKILL_NAME
    moved_outputs: list[tuple[Path, Path, Path]] = []
    try:
        location.path.rename(quarantined_source)
        moved_state, _ = _classify(quarantined_source, location.bundle_hash)
        if (
            _entry_identity(quarantined_source.lstat()) != _entry_identity(source_before)
            or moved_state is not state
        ):
            _restore(quarantined_source, location.path)
            raise LoadoutError(f"{location.path} changed while the uninstall was being applied")

        for path, expected in outputs.items():
            if not _entry_exists(path):
                continue
            if not _matches_output(path, expected):
                raise LoadoutError(f"{path} changed while the uninstall was being applied")
            quarantined, container = _quarantine_file(path)
            moved_outputs.append((path, quarantined, container))

        for _, quarantined, container in moved_outputs:
            quarantined.unlink()
            container.rmdir()
        shutil.rmtree(quarantined_source)
        source_container.rmdir()
    except BaseException:
        for path, quarantined, container in reversed(moved_outputs):
            if quarantined.exists():
                _restore(quarantined, path)
            with suppress(OSError):
                container.rmdir()
        if quarantined_source.exists() and not location.path.exists():
            _restore(quarantined_source, location.path)
        with suppress(OSError):
            source_container.rmdir()
        raise

    removed = tuple(outputs)
    _remove_empty_parents(removed)
    return removed


def uninstall_skill_source(
    location: SkillSourceLocation,
    *,
    root: Path,
    profile: str,
) -> tuple[Path, ...]:
    state, _ = _classify(location.path, location.bundle_hash)
    if state is SourceSkillState.MISSING:
        return ()
    if state is SourceSkillState.CONFLICTING:
        raise LoadoutError(f"{location.path} exists and is not owned by loadout")
    if state is SourceSkillState.MODIFIED:
        raise LoadoutError(f"{location.path} was modified after installation; left unchanged")

    outputs = _skill_outputs(root, profile)
    _validate_outputs(outputs)
    return _remove_source_and_outputs(location, state, outputs)
