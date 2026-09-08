from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import tempfile
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .artifacts import Copied, FrozenFile, Output, _no_symlinks, artifact_destination
from .bundled_skill import SKILL_NAME, bundled_skill_path
from .deployment import apply_deployment, atomic_install, prepare_deployment, read_file
from .discovery import entry_state
from .emit import artifact_deployment_scopes, render_global
from .errors import LoadoutError, UsageError
from .migration_journal import protected
from .migration_models import EntryState
from .skill_installation import (
    OWNER_MARKER,
    SkillSourceLocation,
    SourceSkillState,
    _entry_exists,
    _entry_identity,
    _ExpectedOutput,
    _load_skill_profile,
    _read_marker,
    _rename_without_replacing,
    _restore,
    _skill_outputs,
    _write_marker,
    inspect_skill_source,
)
from .templates import copy_tree, tree_hash


@dataclass(frozen=True)
class SkillTarget:
    location: SkillSourceLocation
    metadata: Path
    agents: tuple[str, ...]
    destinations: tuple[Path, ...] = ()
    configuration: tuple[EntryState, ...] = ()
    ownership: FrozenFile | None = None


@dataclass(frozen=True)
class SkillCommand:
    action: str
    yes: bool = False


def _native_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for entry in sorted(path.rglob("*")):
        _no_symlinks(entry, path)
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise LoadoutError(f"skill entry must be a regular file: {entry}")
        for value in (
            entry.relative_to(path).as_posix().encode(),
            str(entry.stat().st_mode & 0o777).encode(),
            entry.read_bytes(),
        ):
            digest.update(len(value).to_bytes(8))
            digest.update(value)
    return "sha256:" + digest.hexdigest()


def _target_hash(target: SkillTarget, path: Path) -> str:
    return tree_hash(path) if target.metadata == target.location.path else _native_hash(path)


def _state(path: Path, metadata: Path, digest: str) -> tuple[SourceSkillState, str | None]:
    recorded = _read_marker(metadata)
    if not _entry_exists(path):
        if metadata != path and _entry_exists(metadata / OWNER_MARKER) and recorded is None:
            return SourceSkillState.CONFLICTING, None
        return SourceSkillState.MISSING, recorded
    if path.is_symlink() or not path.is_dir() or recorded is None:
        return SourceSkillState.CONFLICTING, recorded
    if (tree_hash(path) if metadata == path else _native_hash(path)) != recorded:
        return SourceSkillState.MODIFIED, recorded
    return (
        SourceSkillState.INSTALLED if recorded == digest else SourceSkillState.UPDATE_AVAILABLE
    ), recorded


def inspect_native_targets(
    root: Path, profile: str, bundle: Path, source_name: str | None
) -> tuple[SkillTarget, ...]:
    manifest = _load_skill_profile(root, profile)
    assert manifest.artifacts is not None
    configuration = tuple(
        entry_state(path) for path in (*manifest.config_paths, manifest.artifacts.path)
    )
    routes: dict[Path, set[str]] = {}
    destinations: dict[Path, list[Path]] = {}
    for record in manifest.artifacts.records:
        if not any(part.category == "skills" for part in record.parts):
            continue
        if record.format != "tree" or artifact_destination(record).name != "skills":
            raise LoadoutError(
                f"{record.label}: bundled installation needs a skill-directory tree route; resolve its producer with the loadout skill"
            )
        source = manifest.artifacts.source_root / record.parts[0].source
        if source.is_symlink() or any(parent.is_symlink() for parent in source.parents):
            raise LoadoutError(f"skill source must not traverse a symlink: {source}")
        for reserved in (root / ".loadout-state", root / ".loadout-bundles"):
            if reserved.is_relative_to(source):
                raise LoadoutError(
                    f"skill tree contains installer metadata location: {source}; declare a dedicated skill subtree"
                )
        routes.setdefault(source / SKILL_NAME, set()).update(record.agents)
        destinations.setdefault(source / SKILL_NAME, []).append(
            artifact_destination(record) / SKILL_NAME
        )
    digest = _native_hash(bundle)
    targets = []
    for path, agents in routes.items():
        key = hashlib.sha256(
            str(path.relative_to(manifest.artifacts.source_root)).encode()
        ).hexdigest()
        metadata = root / ".loadout-bundles" / key
        ownership = read_file(metadata / OWNER_MARKER)
        state, recorded = _state(path, metadata, digest)
        location = SkillSourceLocation(
            f"artifact:{path.relative_to(manifest.artifacts.source_root)}",
            path,
            state,
            recorded,
            digest,
        )
        targets.append(
            SkillTarget(
                location,
                metadata,
                tuple(sorted(agents)),
                tuple(destinations[path]),
                configuration,
                ownership,
            )
        )
    if manifest.skills:
        legacy = inspect_skill_source(root, profile, bundle, source_name)
        if legacy.path not in routes:
            targets.append(
                SkillTarget(
                    legacy,
                    legacy.path,
                    tuple(sorted({t.agent for t in manifest.skills})),
                    configuration=configuration,
                )
            )
    elif source_name is not None:
        raise UsageError(
            "--source selects a legacy named source; native skill trees are selected by their active artifact routes"
        )
    for target in targets:
        if any(
            target.location.path != other.location.path
            and target.location.path.is_relative_to(other.location.path)
            for other in targets
        ):
            raise LoadoutError(f"overlapping skill source targets: {target.location.path}")
    return tuple(targets)


def _validate(targets: tuple[SkillTarget, ...]) -> None:
    _validate_configuration(targets)
    for target in targets:
        location = target.location
        if target.metadata != location.path:
            marker = target.metadata / OWNER_MARKER
            if read_file(marker) != target.ownership:
                raise LoadoutError(f"skill owner metadata changed after preview: {marker}")
        state, recorded = _state(location.path, target.metadata, location.bundle_hash)
        if state in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
            detail = (
                "not owned by loadout"
                if state == SourceSkillState.CONFLICTING
                else "modified after installation"
            )
            raise LoadoutError(f"{location.path}: {detail}; no changes made")
        if (state, recorded) != (location.state, location.recorded_hash):
            raise LoadoutError(f"skill source changed after preview: {location.path}")


def _validate_configuration(targets: tuple[SkillTarget, ...]) -> None:
    for target in targets:
        for state in target.configuration:
            if entry_state(state.path) != state:
                raise LoadoutError(f"skill configuration changed after preview: {state.path}")


@dataclass
class _SourceChange:
    target: SkillTarget
    quarantine: Path
    installed: bool = False
    parents: tuple[EntryState, ...] = ()


def _parent_states(path: Path) -> tuple[EntryState, ...]:
    _no_symlinks(path.parent)
    return tuple(entry_state(parent, read=False) for parent in path.parents if parent.is_dir())


def _same_parents(path: Path, states: tuple[EntryState, ...]) -> bool:
    try:
        _no_symlinks(path.parent)
        return all(entry_state(state.path, read=False) == state for state in states)
    except (LoadoutError, OSError):
        return False


def _restore_source(change: _SourceChange) -> bool:
    location = change.target.location
    if not _same_parents(location.path, change.parents):
        return False
    if change.installed and _entry_exists(location.path):
        if (
            location.path.is_symlink()
            or _target_hash(change.target, location.path) != location.bundle_hash
            or not _same_parents(location.path, change.parents)
        ):
            return False
        shutil.rmtree(location.path)
    if change.quarantine.exists():
        if not _same_parents(location.path, change.parents):
            return False
        try:
            _restore(change.quarantine, location.path)
        except LoadoutError:
            return False
    return True


class _Changes:
    def __init__(self, work: Path) -> None:
        self.work = work
        self.files: list[tuple[Path, FrozenFile | None, FrozenFile | None]] = []
        self.sources: list[_SourceChange] = []
        self.parents: dict[Path, tuple[EntryState, ...]] = {}

    def save(self) -> None:
        def image(value: FrozenFile | None) -> dict[str, str | int] | None:
            return (
                None
                if value is None
                else {"content": base64.b64encode(value.content).decode(), "mode": value.mode}
            )

        data = {
            "sources": [
                {
                    "path": str(c.target.location.path),
                    "quarantine": str(c.quarantine),
                    "installed": c.installed,
                    "parents": [str(p.path) for p in c.parents],
                }
                for c in self.sources
            ],
            "files": [
                {"path": str(p), "before": image(b), "after": image(a)} for p, b, a in self.files
            ],
        }
        protected(self.work, tracking=False)
        atomic_install(self.work / "recovery.json", FrozenFile(json.dumps(data).encode(), 0o600))

    def write(self, path: Path, after: FrozenFile | None) -> None:
        self.write_checked(path, read_file(path), after)

    def write_checked(
        self, path: Path, before: FrozenFile | None, after: FrozenFile | None
    ) -> None:
        parents = _parent_states(path)
        if read_file(path) != before:
            raise LoadoutError(f"skill output changed during update: {path}")
        if before == after:
            return
        self.files.append((path, before, after))
        self.parents[path] = parents
        self.save()
        try:
            if read_file(path) != before:
                raise LoadoutError(f"skill output changed during update: {path}")
        except BaseException:
            self.files.pop()
            raise
        if after is None:
            path.unlink()
        else:
            atomic_install(path, after)
        if not _same_parents(path, parents):
            raise LoadoutError(f"skill output parent changed during update: {path}")
        self.parents[path] = _parent_states(path)

    def restore(self) -> tuple[Path, ...]:
        conflicts = []
        for path, before, after in reversed(self.files):
            if not _same_parents(path, self.parents[path]):
                conflicts.append(path)
                continue
            current = read_file(path)
            if current == before:
                continue
            if current != after or not _same_parents(path, self.parents[path]):
                conflicts.append(path)
            elif before is None:
                path.unlink(missing_ok=True)
            else:
                atomic_install(path, before)
        for change in reversed(self.sources):
            if not _restore_source(change):
                conflicts.append(change.target.location.path)
        return tuple(conflicts)


def _replace_source(change: _SourceChange, bundle: Path, staged: Path | None) -> None:
    target, location = change.target, change.target.location
    _no_symlinks(location.path)
    location.path.parent.mkdir(parents=True, exist_ok=True)
    change.parents = _parent_states(location.path)
    if _entry_exists(location.path):
        before = location.path.lstat()
        _rename_without_replacing(location.path, change.quarantine)
        if (
            _entry_identity(change.quarantine.lstat()) != _entry_identity(before)
            or _target_hash(target, change.quarantine) != location.recorded_hash
        ):
            raise LoadoutError(f"skill source changed during update: {location.path}")
    if staged is not None:
        if target.metadata == location.path:
            copy_tree(bundle, staged)
            _write_marker(staged, location.bundle_hash)
        else:
            shutil.copytree(bundle, staged)
        if _target_hash(target, staged) != location.bundle_hash:
            raise LoadoutError("bundled skill changed while staging")
        _rename_without_replacing(staged, location.path)
        change.installed = True


def change_native_targets(
    root: Path, profile: str, targets: tuple[SkillTarget, ...], bundle: Path, *, uninstall: bool
) -> None:
    _validate(targets)
    scopes = artifact_deployment_scopes(root, profile)
    outputs = render_global(root, profile)
    before = prepare_deployment(scopes, _proposed(outputs, targets, bundle, uninstall=uninstall))
    if before.conflicts:
        raise LoadoutError("; ".join(before.conflicts))
    legacy_before = _skill_outputs(root, profile)
    legacy_preimages = _freeze_legacy(legacy_before)
    if any(_target_hash(target, bundle) != target.location.bundle_hash for target in targets):
        raise LoadoutError("bundled skill changed after preview")
    directory = root / ".loadout-state"
    protected(directory)
    directory.mkdir(mode=0o700, exist_ok=True)
    ignored = directory / ".gitignore"
    if not ignored.exists():
        atomic_install(ignored, FrozenFile(b"*\n", 0o600))
    work = Path(tempfile.mkdtemp(prefix="skill-", dir=directory))
    changes = _Changes(work)
    retain = False
    try:
        _change_sources(targets, bundle, changes, work, uninstall=uninstall)
        _validate_configuration(targets)
        deployment = prepare_deployment(scopes, render_global(root, profile))
        legacy_after = _skill_outputs(root, profile)
        _validate_legacy_preimages(legacy_preimages, legacy_after)
        apply_deployment(deployment, write=changes.write)
        _write_legacy(changes, legacy_preimages, legacy_after)
    except BaseException as error:
        retain = True
        try:
            conflicts = changes.restore()
        except BaseException as recovery_error:
            raise LoadoutError(
                f"skill rollback interrupted; recovery originals retained at {work}"
            ) from recovery_error
        if conflicts:
            raise LoadoutError(
                f"skill update interrupted; recovery originals retained at {work}; conflicts: {', '.join(map(str, conflicts))}"
            ) from error
        retain = False
        raise
    finally:
        if not retain:
            shutil.rmtree(work)
    for target in targets:
        if uninstall:
            with suppress(OSError):
                target.metadata.rmdir()


def _proposed(
    outputs: dict[Path, Output], targets: tuple[SkillTarget, ...], bundle: Path, *, uninstall: bool
) -> dict[Path, Output]:
    result = dict(outputs)
    for target in targets:
        for destination in target.destinations:
            result = {
                path: output
                for path, output in result.items()
                if not path.is_relative_to(destination)
            }
            if not uninstall:
                for source in bundle.rglob("*"):
                    if source.is_file():
                        result[destination / source.relative_to(bundle)] = Copied(source)
    return result


def _freeze_legacy(outputs: dict[Path, _ExpectedOutput]) -> dict[Path, FrozenFile | None]:
    frozen = {}
    for path, expected in outputs.items():
        current = read_file(path)
        if current is not None and (
            current.content != expected.content
            or (
                expected.executable is not None
                and bool(current.mode & 0o111) != expected.executable
            )
        ):
            raise LoadoutError(f"{path} was modified outside loadout; no changes made")
        frozen[path] = current
    return frozen


def _write_legacy(
    changes: _Changes,
    before: dict[Path, FrozenFile | None],
    after: dict[Path, _ExpectedOutput],
) -> None:
    for path in sorted(before.keys() | after.keys()):
        output = after.get(path)
        content = (
            FrozenFile(output.content, 0o755 if output.executable else 0o644)
            if output is not None
            else None
        )
        changes.write_checked(path, before.get(path), content)


def _validate_legacy_preimages(
    before: dict[Path, FrozenFile | None], after: dict[Path, _ExpectedOutput]
) -> None:
    for path, expected in before.items():
        if read_file(path) != expected:
            raise LoadoutError(f"skill output changed during update: {path}")
    for path in after.keys() - before.keys():
        if read_file(path) is not None:
            raise LoadoutError(f"unowned skill output already exists: {path}")
        before[path] = None


def _change_sources(
    targets: tuple[SkillTarget, ...],
    bundle: Path,
    changes: _Changes,
    work: Path,
    *,
    uninstall: bool,
) -> None:
    for index, target in enumerate(targets):
        location = target.location
        if not uninstall and location.state == SourceSkillState.INSTALLED:
            continue
        _validate((target,))
        if not uninstall or location.state != SourceSkillState.MISSING:
            change = _SourceChange(target, work / f"original-{index}")
            changes.sources.append(change)
            changes.save()
            _replace_source(change, bundle, None if uninstall else work / f"staged-{index}")
            changes.save()
        if target.metadata == location.path:
            continue
        marker = target.metadata / OWNER_MARKER
        content = (
            None
            if uninstall
            else FrozenFile(
                f'owner = "loadout-bundled-skill"\nhash = "{location.bundle_hash}"\n'.encode(),
                0o644,
            )
        )
        changes.write_checked(marker, target.ownership, content)


def run_native_skill(
    root: Path,
    profile: str,
    source_name: str | None,
    *,
    command: SkillCommand,
    sync: Callable[[], int] | None = None,
) -> int:
    action, yes = command.action, command.yes
    bundle = bundled_skill_path()
    targets = inspect_native_targets(root, profile, bundle, source_name)
    for target in targets:
        print(
            f"source {target.location.source}: {target.location.path} ({target.location.state.value}); agents: {', '.join(target.agents)}"
        )
    if not targets:
        print(
            "No configured skill routes; declare a global skill tree before installing the bundle."
        )
        return 0
    if action == "status":
        return 0
    try:
        _validate(targets)
        if not yes:
            try:
                response = input(
                    f"{action.capitalize()} the displayed loadout skill sources and sync global config? [y/N] "
                )
            except (EOFError, OSError) as error:
                raise UsageError(
                    f"skill {action} requires --yes when input is not interactive"
                ) from error
            if response.strip().lower() not in {"y", "yes"}:
                print("declined; no changes made")
                return 0
        change_native_targets(root, profile, targets, bundle, uninstall=action == "uninstall")
    except UsageError:
        raise
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 1
    print(
        f"{'uninstalled' if action == 'uninstall' else 'installed'} loadout skill across {len(targets)} source trees"
    )
    return sync() if sync is not None else 0
