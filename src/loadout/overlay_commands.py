from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import machine
from .commands import check_result, cmd_check, cmd_sync
from .errors import LoadoutError, UsageError
from .git_runner import toplevel
from .manifest import manifest_path
from .overlay_check import OverlayCheck, check_overlay, report_check
from .overlay_lock import same_path
from .overlay_manifest import holds_overlay, load_overlay_manifest
from .overlay_record import (
    NEWER_RECORD,
    OverlayRecord,
    every_overlay_record,
    overlay_record_path,
    read_overlay_record,
    record_files,
    record_problem_message,
    scan_overlay_records,
)
from .overlay_registry import (
    NAME_PATTERN,
    RegisteredOverlay,
    UnreadableRegistry,
    find_by_folder,
    load_registry,
    marked_removing,
    overlays_path,
    update_registry,
    with_overlay,
    without_overlay,
)
from .overlay_sync import OverlayRun, report, sync_overlay
from .overlay_targets import expand_patterns, harness_homes
from .project import project_config_path

PROFILE_REFUSAL = "overlays render the default profile only; --profile applies to a loadout source"
_YES = frozenset({"y", "yes"})


@dataclass(frozen=True)
class Selection:
    root: Path | None
    profile: str | None
    global_error: str | None
    overlays: tuple[tuple[RegisteredOverlay, Path | None], ...]
    overlay_force: bool
    registry_error: str | None = None


def _record_holder(folder: Path, registered: set[str]) -> str | None:
    scan = scan_overlay_records()
    if scan.problems:
        raise LoadoutError(record_problem_message(scan.problems[0]))
    return next(
        (
            record.name
            for record in scan.records
            if same_path(record.folder, folder) and record.name not in registered
        ),
        None,
    )


def cmd_overlay_add(directory: Path | None, name: str | None) -> int:
    folder = (directory or Path.cwd()).resolve()
    load_overlay_manifest(folder)
    chosen = name if name is not None else folder.name.lower()
    if NAME_PATTERN.fullmatch(chosen) is None:
        raise UsageError(
            f"{chosen!r} is not a valid overlay name (lowercase letters, digits and "
            f"hyphens, starting with a letter or digit); pass --name"
        )
    registered = load_registry(overlays_path())
    if machine.is_registered(folder, machine.registration().source):
        raise LoadoutError(f"{folder} is this machine's registered global source, not an overlay")
    if (old := _record_holder(folder, {o.name for o in registered})) is not None and old != chosen:
        raise LoadoutError(
            f"{folder} has an unregistered overlay record named {old}; "
            f"run `loadout overlay remove {old}` first"
        )
    update_registry(lambda current: with_overlay(current, RegisteredOverlay(chosen, folder)))
    print(
        f"registered overlay {chosen} at {folder}; preview with `loadout check --overlay {chosen}`"
    )
    return 0


def _targets(overlay: RegisteredOverlay) -> str:
    if overlay.removing:
        return f"(removing: run `loadout overlay remove {overlay.name}`)"
    try:
        return ", ".join(load_overlay_manifest(overlay.folder).settings.targets)
    except (LoadoutError, OSError) as error:
        return f"(cannot load: {error})"


def _unregistered(
    registered: Sequence[RegisteredOverlay], records: Sequence[OverlayRecord]
) -> tuple[OverlayRecord, ...]:
    names = {overlay.name for overlay in registered}
    return tuple(
        record
        for record in records
        if record.name not in names and find_by_folder(registered, record.folder) is None
    )


def cmd_overlay_list() -> int:
    registered = load_registry(overlays_path())
    orphans = _unregistered(registered, every_overlay_record())
    if not registered and not orphans:
        print("no overlays registered; add one with `loadout overlay add <folder>`")
        return 0
    for overlay in registered:
        print(f"{overlay.name}  {overlay.folder}  {_targets(overlay)}")
    for record in orphans:
        print(
            f"{record.name}  {record.folder}  (unregistered record; remove it with "
            f"`loadout overlay remove {record.name}`)"
        )
    return 0


def _same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left == right


def _patterns(overlay: RegisteredOverlay) -> tuple[str, ...]:
    try:
        return load_overlay_manifest(overlay.folder).settings.targets
    except (LoadoutError, OSError) as error:
        print(f"WARNING: overlay {overlay.name}: {error}", file=sys.stderr)
        return ()


def _matches(patterns: tuple[str, ...], root: Path, home: Path) -> bool:
    found, _ = expand_patterns(patterns, home)
    return any(_same(folder, root) for folders in found.values() for folder in folders)


def overlays_targeting(root: Path) -> tuple[tuple[RegisteredOverlay, Path], ...]:
    home = harness_homes().home
    return tuple(
        (overlay, root)
        for overlay in load_registry(overlays_path())
        if _matches(_patterns(overlay), root, home)
    )


def _known_names(overlays: Sequence[RegisteredOverlay]) -> str:
    names = ", ".join(overlay.name for overlay in overlays)
    return f"registered overlays: {names}" if names else "no overlays are registered"


def _registered(name: str) -> RegisteredOverlay:
    overlays = load_registry(overlays_path())
    found = next((overlay for overlay in overlays if overlay.name == name), None)
    if found is None:
        raise LoadoutError(f"no overlay named {name} is registered; {_known_names(overlays)}")
    return found


def _overlays_only(
    overlays: tuple[tuple[RegisteredOverlay, Path | None], ...], profile: str | None, force: bool
) -> Selection:
    if profile is not None:
        raise UsageError(PROFILE_REFUSAL)
    return Selection(None, None, None, overlays, force)


def _has_manifest(root: Path) -> bool:
    return manifest_path(root).is_file() or project_config_path(root).is_file()


def _warned(error: Exception) -> tuple[tuple[RegisteredOverlay, Path], ...]:
    print(f"WARNING: overlays: {error}", file=sys.stderr)
    return ()


def _targeting_quietly(
    root: Path, registry: Sequence[RegisteredOverlay]
) -> tuple[tuple[RegisteredOverlay, Path], ...]:
    try:
        if not registry:
            return ()
        top = toplevel(root)
        return overlays_targeting(top) if top is not None else ()
    except (LoadoutError, OSError) as error:
        return _warned(error)


def _local(
    root: Path, profile: str | None, force: bool, resolve: Callable[[], tuple[Path, str]]
) -> Selection:
    manifest_here = _has_manifest(root)
    try:
        registry = load_registry(overlays_path())
    except (LoadoutError, OSError) as error:
        if holds_overlay(root):
            return Selection(None, None, None, (), False, str(error))
        if manifest_here and isinstance(error, OSError | UnreadableRegistry):
            return Selection(*resolve(), None, _warned(error), force)
        return Selection(*resolve(), None, (), force, str(error))
    found = find_by_folder(registry, root)
    if found is not None:
        return _overlays_only(((found, None),), profile, force)
    if holds_overlay(root):
        raise LoadoutError(
            f"{root} holds an overlay; register it with `loadout overlay add {root}`"
        )
    if manifest_here or not registry:
        return Selection(*resolve(), None, _targeting_quietly(root, registry), force)
    top = toplevel(root)
    targeting = overlays_targeting(top) if top is not None else ()
    if not targeting:
        raise LoadoutError(
            f"no manifest found in {root}: expected loadout.toml or loadout/config.toml, "
            f"and no registered overlay targets {top or root}"
        )
    return _overlays_only(targeting, profile, force)


def resolve_selection(  # noqa: PLR0913
    root: Path,
    *,
    use_global: bool,
    overlay: str | None,
    profile: str | None,
    force: bool,
    staged: bool,
    resolve: Callable[[], tuple[Path, str]],
) -> Selection:
    if staged:
        if overlay is not None:
            raise UsageError("--staged covers the project only; it cannot be used with --overlay")
        return Selection(*resolve(), None, (), False)
    if overlay is not None:
        if profile is not None:
            raise UsageError(PROFILE_REFUSAL)
        return Selection(None, None, None, ((_registered(overlay), None),), force)
    if use_global:
        try:
            overlays = tuple((found, None) for found in load_registry(overlays_path()))
            registry_error = None
        except (LoadoutError, OSError) as error:
            overlays, registry_error = (), str(error)
        try:
            source, chosen = resolve()
        except LoadoutError as error:
            return Selection(None, None, str(error), overlays, False, registry_error)
        return Selection(source, chosen, None, overlays, False, registry_error)
    return _local(root.resolve(), profile, force, resolve)


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _root_only(selection: Selection, action: Callable[[Path, str], int]) -> int:
    if selection.global_error is not None:
        raise LoadoutError(selection.global_error)
    if selection.root is None or selection.profile is None:
        raise LoadoutError("nothing selected to sync or check")
    return action(selection.root, selection.profile)


def _problems(selection: Selection) -> tuple[str, ...]:
    return tuple(
        problem
        for problem in (selection.global_error, selection.registry_error)
        if problem is not None
    )


def _described(error: Exception) -> str:
    if isinstance(error, OSError) and error.filename:
        return f"{error.filename}: {error.strerror}"
    return str(error)


def _root_part(selection: Selection, action: Callable[[Path, str], int]) -> int:
    problems = _problems(selection)
    for problem in problems:
        print(f"loadout: {problem}", file=sys.stderr)
    floor = 3 if problems else 0
    if selection.root is None or selection.profile is None:
        return floor
    try:
        code = action(selection.root, selection.profile)
    except (LoadoutError, OSError) as error:
        print(f"loadout: {_described(error)}", file=sys.stderr)
        code = 3
    return max(code, floor)


def _root_alone(selection: Selection) -> bool:
    return not selection.overlays and selection.registry_error is None


def _scoped_first(
    selection: Selection,
) -> tuple[list[tuple[RegisteredOverlay, Path]], list[tuple[RegisteredOverlay, None]]]:
    scoped = [(overlay, scope) for overlay, scope in selection.overlays if scope is not None]
    rest = [(overlay, None) for overlay, scope in selection.overlays if scope is None]
    return scoped, rest


def _sync_one(overlay: RegisteredOverlay, scope: Path | None, force: bool) -> int:
    try:
        outcome = sync_overlay(OverlayRun(overlay, scope, force, _interactive()))
    except (LoadoutError, OSError) as error:
        print(f"overlay {overlay.name}: ERROR: {error}", file=sys.stderr)
        return 3
    report(outcome)
    return outcome.code


def cmd_sync_selection(selection: Selection, *, force: bool) -> int:
    def root_sync(root: Path, profile: str) -> int:
        return cmd_sync(root, profile=profile, force=force)

    if _root_alone(selection):
        return _root_only(selection, root_sync)
    scoped, rest = _scoped_first(selection)
    codes = [
        *(_sync_one(overlay, scope, selection.overlay_force) for overlay, scope in scoped),
        _root_part(selection, root_sync),
        *(_sync_one(overlay, scope, selection.overlay_force) for overlay, scope in rest),
    ]
    return max(codes)


def _checked(overlay: RegisteredOverlay, scope: Path | None) -> OverlayCheck:
    try:
        return check_overlay(overlay, scope)
    except (LoadoutError, OSError) as error:
        return OverlayCheck(overlay.name, (), (), ((None, str(error)),), 3)


def _report_one(check: OverlayCheck) -> int:
    report_check(check)
    return check.code


def _error_entry(overlay: str | None, target: Path | None, message: str) -> dict[str, str | None]:
    return {"overlay": overlay, "target": None if target is None else str(target), "error": message}


def _status(code: int, drift: Sequence[object]) -> str:
    if code >= 3:  # noqa: PLR2004
        return "error"
    return "drift" if drift else "clean"


def _check_json(selection: Selection) -> int:
    errors: list[dict[str, str | None]] = []
    base: dict[str, Any] = {"exit_code": 0, "drift": [], "notes": []}
    errors.extend(_error_entry(None, None, problem) for problem in _problems(selection))
    if selection.root is not None and selection.profile is not None:
        try:
            base = check_result(selection.root, selection.profile)
        except (LoadoutError, OSError) as error:
            errors.append(_error_entry(None, selection.root, _described(error)))
    checks = [_checked(overlay, scope) for overlay, scope in selection.overlays]
    drift = [
        *base["drift"],
        *({"path": str(item.path), "kind": item.kind} for check in checks for item in check.drift),
    ]
    notes = [
        *base["notes"],
        *(f"overlay {check.name}: {note}" for check in checks for note in check.notes),
    ]
    errors.extend(
        _error_entry(check.name, target, message)
        for check in checks
        for target, message in check.errors
    )
    code = max(int(base["exit_code"]), 3 if errors else 0, *(check.code for check in checks))
    result = {
        "status": _status(code, drift),
        "exit_code": code,
        "drift": drift,
        "notes": notes,
        "errors": errors,
    }
    print(json.dumps(result, indent=2))
    return code


def cmd_check_selection(selection: Selection, *, as_json: bool) -> int:
    def root_check(root: Path, profile: str) -> int:
        return cmd_check(root, profile=profile, as_json=as_json)

    if _root_alone(selection):
        return _root_only(selection, root_check)
    if as_json:
        return _check_json(selection)
    scoped, rest = _scoped_first(selection)
    codes = [
        *(_report_one(_checked(overlay, scope)) for overlay, scope in scoped),
        _root_part(selection, root_check),
        *(_report_one(_checked(overlay, scope)) for overlay, scope in rest),
    ]
    return max(codes)


def refuse_targeted_init(root: Path) -> None:
    resolved = root.resolve()
    scan = scan_overlay_records()
    if scan.problems:
        raise LoadoutError(
            "an overlay record cannot be read, so loadout cannot tell whether an overlay writes "
            f"personal files here; {record_problem_message(scan.problems[0])}"
        )
    holder = next(
        (
            record.name
            for record in scan.records
            if any(_same(target, resolved) for target in record.targets)
        ),
        None,
    )
    if holder is not None:
        raise LoadoutError(
            "an overlay writes personal files here; adopting would sweep them into the team's "
            f"source — remove this repository from overlay {holder} first: narrow its `targets`, "
            f"then run `loadout sync --overlay {holder}`"
        )


def _removal_target(name: str) -> tuple[RegisteredOverlay, bool]:
    registered = load_registry(overlays_path())
    found = next((overlay for overlay in registered if overlay.name == name), None)
    if found is not None:
        return found, True
    scan = scan_overlay_records()
    folders = {overlay.folder for overlay in registered}
    record = next((r for r in _unregistered(registered, scan.records) if r.name == name), None)
    if record is not None:
        return RegisteredOverlay(name, record.folder), False
    problem = next(
        (
            p
            for p in scan.problems
            if p.name == name and p.folder is not None and p.folder not in folders
        ),
        None,
    )
    if problem is not None and problem.folder is not None:
        return RegisteredOverlay(name, problem.folder), False
    raise LoadoutError(
        f"no overlay or overlay record named {name}; {_known_names(registered)}; "
        "see `loadout overlay list`"
    )


def _recorded_count(record: OverlayRecord) -> int:
    return len(record.shared) + sum(
        len(entry.files)
        + len(entry.documents)
        + sum(len(leaves) for leaves in entry.leaves.values())
        for entry in record.targets.values()
    )


def _confirmed(name: str, count: int, yes: bool) -> bool:
    if yes:
        return True
    if not _interactive():
        raise UsageError(f"overlay remove {name} needs --yes when input is not interactive")
    noun = "output" if count == 1 else "outputs"
    try:
        answer = input(f"remove overlay {name} and retire its {count} recorded {noun}? [y/N] ")
    except (EOFError, OSError) as error:
        raise UsageError(
            f"overlay remove {name} needs --yes when input is not interactive"
        ) from error
    return answer.strip().lower() in _YES


def _finisher(overlay: RegisteredOverlay, registered: bool) -> Callable[[], None]:
    def finish() -> None:
        for path in record_files(overlay.folder):
            path.unlink(missing_ok=True)
        if registered:
            update_registry(lambda current: without_overlay(current, overlay.name))

    return finish


def _left(overlay: RegisteredOverlay, registered: bool) -> tuple[str, ...]:
    try:
        registration = registered and any(
            found.name == overlay.name for found in load_registry(overlays_path())
        )
    except (LoadoutError, OSError):
        registration = registered
    try:
        record = bool(record_files(overlay.folder))
    except OSError:
        record = True
    return (*(["registration"] if registration else []), *(["record"] if record else []))


def _failed_removal(overlay: RegisteredOverlay, registered: bool) -> str:
    left = _left(overlay, registered)
    if not left:
        return (
            "removed — its registration and record are gone, but the step above failed, so the "
            "global gitignore block may still list its files until another overlay syncs"
        )
    kept = " and ".join(left)
    verb = "is" if len(left) == 1 else "are"
    return (
        f"not removed; the {kept} {verb} kept — fix the problems above, then run "
        f"`loadout overlay remove {overlay.name}` again"
    )


def cmd_overlay_remove(name: str, *, force: bool, yes: bool) -> int:
    overlay, registered = _removal_target(name)
    record, status = read_overlay_record(overlay.folder, name, harness_homes().home)
    path = overlay_record_path(overlay.folder)
    if status == "newer":
        raise LoadoutError(f"the overlay record {path} {NEWER_RECORD}")
    if status == "unreadable" and not force:
        raise LoadoutError(
            f"the overlay record {path} cannot be read; fix it, or run "
            f"`loadout overlay remove {name} --force` to treat it as empty"
        )
    if not _confirmed(name, _recorded_count(record), yes):
        print("declined; no changes made")
        return 0
    if registered:
        update_registry(lambda current: marked_removing(current, name))
    run = OverlayRun(
        overlay=replace(overlay, removing=True),
        scope=None,
        force=force,
        interactive=False,
        retire_all=True,
        finish=_finisher(overlay, registered),
    )
    outcome = sync_overlay(run)
    report(outcome)
    if outcome.code:
        print(f"overlay {name}: {_failed_removal(overlay, registered)}", file=sys.stderr)
        return outcome.code
    print(f"removed overlay {name}")
    return 0
