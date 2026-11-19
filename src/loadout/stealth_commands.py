from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import machine
from .commands import check_result, cmd_check
from .errors import LoadoutError, UsageError
from .exclude import exclude_path
from .git_runner import toplevel
from .kit_fanout import (
    check_plain,
    check_projects,
    check_projects_json,
    project_index,
    retire_plain,
    sync_plain,
    sync_projects,
    sync_with_push,
    warn,
)
from .manifest import manifest_path
from .overlay_leftovers import (
    OverlayLeftover,
    UnreadableOverlayRegistry,
    check_exclude,
    check_registry,
    registry_notice,
)
from .personal import adopted_target, stranded_target
from .project import project_config_path
from .stealth_check import StealthCheck, check_stealth, report_check
from .stealth_git import GitFailure, identity
from .stealth_lock import same_path, stealth_lock
from .stealth_manifest import holds_stealth, load_stealth_manifest
from .stealth_record import (
    NEWER_RECORD,
    StealthRecord,
    every_stealth_record,
    read_stealth_record,
    record_files,
    record_problem_message,
    scan_stealth_records,
    stealth_record_path,
)
from .stealth_registry import (
    NAME_PATTERN,
    RegisteredStealth,
    UnreadableRegistry,
    find_by_folder,
    load_registry,
    marked_removing,
    stealth_path,
    update_registry,
    with_stealth,
    without_stealth,
)
from .stealth_sync import StealthRun, adopted_roots, report, shown, sync_stealth
from .stealth_targets import harness_homes, matches_root, refuse_patterns_inside
from .written import prune_vanished, record_names_stealth, roots_naming_stealth

PROFILE_REFUSAL = (
    "stealth sources render the default profile only; --profile applies to a loadout source"
)
_YES = frozenset({"y", "yes"})


@dataclass(frozen=True)
class Selection:
    root: Path | None
    profile: str | None
    global_error: str | None
    stealth_sources: tuple[tuple[RegisteredStealth, Path | None], ...]
    stealth_force: bool
    registry_error: str | None = None
    # The root is this machine's global source, so the run also reaches every project
    # that uses a kit (ADR 0057).
    fan_out: bool = False


def _record_holder(folder: Path, registered: set[str]) -> str | None:
    scan = scan_stealth_records()
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


def refuse_overlay_registry() -> None:
    check_registry(machine.config_dir())


def stealth_name(folder: Path, name: str | None) -> str:
    chosen = name if name is not None else folder.name.lower()
    if NAME_PATTERN.fullmatch(chosen) is None:
        raise UsageError(
            f"{chosen!r} is not a valid stealth name (lowercase letters, digits and "
            f"hyphens, starting with a letter or digit); pass --name"
        )
    return chosen


def refuse_registration(folder: Path, chosen: str) -> None:
    registered = load_registry(stealth_path())
    if machine.is_registered(folder, machine.registration().source):
        raise LoadoutError(
            f"{folder} is this machine's registered global source, not a stealth source"
        )
    if (old := _record_holder(folder, {o.name for o in registered})) is not None and old != chosen:
        raise LoadoutError(
            f"{folder} has an unregistered stealth record named {old}; "
            f"run `loadout stealth remove {old}` first"
        )
    with_stealth(registered, RegisteredStealth(chosen, folder))


def register_stealth(folder: Path, chosen: str) -> None:
    refuse_registration(folder, chosen)
    update_registry(lambda current: with_stealth(current, RegisteredStealth(chosen, folder)))
    print(
        f"registered stealth source {chosen} at {folder}; preview with `loadout check --stealth {chosen}`"
    )


def cmd_stealth_add(directory: Path | None, name: str | None) -> int:
    refuse_overlay_registry()
    folder = (directory or Path.cwd()).resolve()
    settings = load_stealth_manifest(folder).settings
    refuse_patterns_inside(settings, harness_homes().home, folder)
    register_stealth(folder, stealth_name(folder, name))
    return 0


def _targets(stealth: RegisteredStealth) -> str:
    if stealth.removing:
        return f"(removing: run `loadout stealth remove {stealth.name}`)"
    try:
        return ", ".join(load_stealth_manifest(stealth.folder).settings.targets)
    except (LoadoutError, OSError) as error:
        return f"(cannot load: {error})"


def _unregistered(
    registered: Sequence[RegisteredStealth], records: Sequence[StealthRecord]
) -> tuple[StealthRecord, ...]:
    names = {stealth.name for stealth in registered}
    return tuple(
        record
        for record in records
        if record.name not in names and find_by_folder(registered, record.folder) is None
    )


def cmd_stealth_list() -> int:
    registered = load_registry(stealth_path())
    orphans = _unregistered(registered, every_stealth_record())
    if not registered and not orphans:
        print("no stealth sources registered; add one with `loadout stealth add <folder>`")
    for stealth in registered:
        print(f"{stealth.name}  {stealth.folder}  {_targets(stealth)}")
    for record in orphans:
        print(
            f"{record.name}  {record.folder}  (unregistered record; remove it with "
            f"`loadout stealth remove {record.name}`)"
        )
    notice = registry_notice(machine.config_dir())
    if notice is not None:
        print(f"WARNING: {notice}", file=sys.stderr)
    return 0


def _same(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return left == right


def _patterns(stealth: RegisteredStealth) -> tuple[str, ...]:
    try:
        return load_stealth_manifest(stealth.folder).settings.targets
    except (LoadoutError, OSError) as error:
        print(f"WARNING: stealth {stealth.name}: {error}", file=sys.stderr)
        return ()


def stealth_targeting(root: Path) -> tuple[tuple[RegisteredStealth, Path], ...]:
    home = harness_homes().home
    return tuple(
        (stealth, root)
        for stealth in load_registry(stealth_path())
        if matches_root(_patterns(stealth), root, home)
    )


def _known_names(stealth_sources: Sequence[RegisteredStealth]) -> str:
    names = ", ".join(stealth.name for stealth in stealth_sources)
    return f"registered stealth sources: {names}" if names else "no stealth sources are registered"


def registered_by_name(name: str) -> RegisteredStealth:
    stealth_sources = load_registry(stealth_path())
    found = next((stealth for stealth in stealth_sources if stealth.name == name), None)
    if found is None:
        raise LoadoutError(
            f"no stealth source named {name} is registered; {_known_names(stealth_sources)}"
        )
    return found


def _stealth_only(
    stealth_sources: tuple[tuple[RegisteredStealth, Path | None], ...],
    profile: str | None,
    force: bool,
) -> Selection:
    if profile is not None:
        raise UsageError(PROFILE_REFUSAL)
    return Selection(None, None, None, stealth_sources, force)


def _has_manifest(root: Path) -> bool:
    return manifest_path(root).is_file() or project_config_path(root).is_file()


def _warned(error: Exception) -> tuple[tuple[RegisteredStealth, Path], ...]:
    print(f"WARNING: stealth sources: {error}", file=sys.stderr)
    return ()


def _targeting_quietly(
    root: Path, registry: Sequence[RegisteredStealth]
) -> tuple[tuple[RegisteredStealth, Path], ...]:
    try:
        if not registry:
            return ()
        top = toplevel(root)
        return stealth_targeting(top) if top is not None else ()
    except (LoadoutError, OSError) as error:
        return _warned(error)


def _local(
    root: Path, profile: str | None, force: bool, resolve: Callable[[], tuple[Path, str]]
) -> Selection:
    manifest_here = _has_manifest(root)
    try:
        refuse_overlay_registry()
        registry = load_registry(stealth_path())
    except (LoadoutError, OSError) as error:
        if isinstance(error, OverlayLeftover) and not manifest_here and not holds_stealth(root):
            raise
        if holds_stealth(root):
            return Selection(None, None, None, (), False, str(error))
        if manifest_here and isinstance(
            error, OSError | UnreadableRegistry | UnreadableOverlayRegistry
        ):
            return Selection(*resolve(), None, _warned(error), force)
        return Selection(*resolve(), None, (), force, str(error))
    found = find_by_folder(registry, root)
    if found is not None:
        return _stealth_only(((found, None),), profile, force)
    if holds_stealth(root):
        raise LoadoutError(
            f"{root} holds a stealth source; register it with `loadout stealth add {root}`"
        )
    if manifest_here or not registry:
        return Selection(*resolve(), None, _targeting_quietly(root, registry), force)
    top = toplevel(root)
    targeting = stealth_targeting(top) if top is not None else ()
    if not targeting:
        raise LoadoutError(
            f"no manifest found in {root}: expected loadout.toml or loadout/config.toml, "
            f"and no registered stealth source targets {top or root}"
        )
    return _stealth_only(targeting, profile, force)


def resolve_selection(  # noqa: PLR0913
    root: Path,
    *,
    use_global: bool,
    stealth: str | None,
    profile: str | None,
    force: bool,
    staged: bool,
    resolve: Callable[[], tuple[Path, str]],
) -> Selection:
    if staged:
        if stealth is not None:
            raise UsageError("--staged covers the project only; it cannot be used with --stealth")
        return Selection(*resolve(), None, (), False)
    if stealth is not None:
        if profile is not None:
            raise UsageError(PROFILE_REFUSAL)
        refuse_overlay_registry()
        return Selection(None, None, None, ((registered_by_name(stealth), None),), force)
    if use_global:
        try:
            refuse_overlay_registry()
            stealth_sources = tuple((found, None) for found in load_registry(stealth_path()))
            registry_error = None
        except (LoadoutError, OSError) as error:
            stealth_sources, registry_error = (), str(error)
        try:
            source, chosen = resolve()
        except LoadoutError as error:
            return Selection(
                None, None, str(error), stealth_sources, False, registry_error, fan_out=True
            )
        return Selection(source, chosen, None, stealth_sources, False, registry_error, fan_out=True)
    selection = _local(root.resolve(), profile, force, resolve)
    return replace(selection, fan_out=_global_source(selection.root))


def _global_source(root: Path | None) -> bool:
    return root is not None and machine.is_registered(root, machine.registration().source)


def interactive() -> bool:
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
    return (
        not selection.stealth_sources and selection.registry_error is None and not selection.fan_out
    )


def _scoped_first(
    selection: Selection,
) -> tuple[list[tuple[RegisteredStealth, Path]], list[tuple[RegisteredStealth, None]]]:
    scoped = [(stealth, scope) for stealth, scope in selection.stealth_sources if scope is not None]
    rest = [(stealth, None) for stealth, scope in selection.stealth_sources if scope is None]
    return scoped, rest


def _sync_one(stealth: RegisteredStealth, scope: Path | None, force: bool) -> int:
    try:
        outcome = sync_stealth(StealthRun(stealth, scope, force, interactive()))
    except (LoadoutError, OSError) as error:
        print(f"stealth {stealth.name}: ERROR: {error}", file=sys.stderr)
        return 3
    report(outcome)
    return outcome.code


def _ask_terminal(question: str) -> bool:
    try:
        return input(question).strip().lower() in _YES
    except (EOFError, OSError):
        return False


def _projects(selection: Selection) -> tuple[Path, ...]:
    """The projects a run at the global source also reaches, never the source itself."""
    if not selection.fan_out:
        return ()
    index = project_index(skip=selection.root)
    warn(index)
    return index.roots


@dataclass(frozen=True)
class Clones:
    adopted: tuple[Path, ...] = ()
    stranded: tuple[Path, ...] = ()
    held: tuple[Path, ...] = ()
    listed: bool = True

    def without(self, skip: Sequence[Path | None]) -> Clones:
        skipped = {os.path.realpath(path) for path in skip if path is not None}

        def kept(roots: tuple[Path, ...]) -> tuple[Path, ...]:
            return tuple(root for root in roots if os.path.realpath(root) not in skipped)

        return replace(
            self,
            adopted=kept(self.adopted),
            stranded=kept(self.stranded),
            held=kept(self.held),
        )


def _clone_kind(root: Path) -> str:
    if adopted_target(root):
        return "adopted"
    return "stranded" if stranded_target(root) else "held"


def adopted_clones(sources: Sequence[RegisteredStealth]) -> Clones:
    listed = True
    prune_vanished()
    try:
        named = [root for stealth in sources for root in roots_naming_stealth(stealth.name)]
    except (LoadoutError, OSError) as error:
        print(
            f"WARNING: cannot list the projects this machine has synced: {error}", file=sys.stderr
        )
        named, listed = [], False
    found = [*(root for stealth in sources for root in adopted_roots(stealth)), *named]
    chosen: dict[str, tuple[Path, str]] = {}
    for root in found:
        key = os.path.realpath(root)
        if key not in chosen:
            chosen[key] = (root, _clone_kind(root))

    def of(kind: str) -> tuple[Path, ...]:
        return tuple(root for root, sort in chosen.values() if sort == kind)

    return Clones(adopted=of("adopted"), stranded=of("stranded"), held=of("held"), listed=listed)


def _retire_stranded(clones: Clones) -> int:
    for root in clones.held:
        print(
            f"note: project {shown(root)}: skipped; its record names a stealth source, but it "
            "is not a safe top-level checkout git can read, so its personal files stay until it is"
        )
    return retire_plain(clones.stranded)


def _unscoped_clones(selection: Selection) -> Clones:
    unscoped = [stealth for stealth, scope in selection.stealth_sources if scope is None]
    if not unscoped:
        return Clones()
    return adopted_clones(unscoped).without((selection.root,))


def _adopted_projects(selection: Selection, kit: Sequence[Path]) -> tuple[Path, ...]:
    return _unscoped_clones(selection).without(kit).adopted


def cmd_sync_selection(selection: Selection, *, force: bool) -> int:
    def root_sync(root: Path, profile: str) -> int:
        ask = _ask_terminal if interactive() else None
        return sync_with_push(root, profile, force=force, ask=ask)

    if _root_alone(selection):
        return _root_only(selection, root_sync)
    scoped, rest = _scoped_first(selection)
    clones = _unscoped_clones(selection)
    codes = [
        _retire_stranded(clones),
        *(_sync_one(stealth, scope, selection.stealth_force) for stealth, scope in scoped),
        _root_part(selection, root_sync),
        *(_sync_one(stealth, scope, selection.stealth_force) for stealth, scope in rest),
    ]
    kit = _projects(selection)
    codes.append(sync_projects(kit))
    codes.append(sync_plain(clones.without(kit).adopted))
    return max(codes)


def _checked(stealth: RegisteredStealth, scope: Path | None) -> StealthCheck:
    try:
        return check_stealth(stealth, scope)
    except (LoadoutError, OSError) as error:
        return StealthCheck(stealth.name, (), (), ((None, str(error)),), 3)


def _report_one(check: StealthCheck) -> int:
    report_check(check)
    return check.code


def _error_entry(stealth: str | None, target: Path | None, message: str) -> dict[str, str | None]:
    return {"stealth": stealth, "target": None if target is None else str(target), "error": message}


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
    checks = [_checked(stealth, scope) for stealth, scope in selection.stealth_sources]
    drift = [
        *base["drift"],
        *({"path": str(item.path), "kind": item.kind} for check in checks for item in check.drift),
    ]
    notes = [
        *base["notes"],
        *(f"stealth {check.name}: {note}" for check in checks for note in check.notes),
    ]
    errors.extend(
        _error_entry(check.name, target, message)
        for check in checks
        for target, message in check.errors
    )
    kit = _projects(selection)
    projects = (
        *check_projects_json(kit),
        *check_projects_json(_adopted_projects(selection, kit), previews=False),
    )
    drift.extend(item for project in projects for item in project.drift)
    notes.extend(
        f"project {project.root}: {note}" for project in projects for note in project.notes
    )
    errors.extend(
        _error_entry(None, project.root, project.error)
        for project in projects
        if project.error is not None
    )
    code = max(
        int(base["exit_code"]),
        3 if errors else 0,
        *(check.code for check in checks),
        *(project.exit_code for project in projects),
    )
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
        *(_report_one(_checked(stealth, scope)) for stealth, scope in scoped),
        _root_part(selection, root_check),
        *(_report_one(_checked(stealth, scope)) for stealth, scope in rest),
    ]
    kit = _projects(selection)
    codes.append(check_projects(kit))
    codes.append(check_plain(_adopted_projects(selection, kit)))
    return max(codes)


def _refuse_overlay_block(root: Path) -> None:
    try:
        found = identity(root)
    except GitFailure:
        return
    if found is not None:
        check_exclude(exclude_path(found.common_dir))


def refuse_targeted_init(root: Path) -> None:
    resolved = root.resolve()
    _refuse_overlay_block(resolved)
    scan = scan_stealth_records()
    if scan.problems:
        raise LoadoutError(
            "a stealth record cannot be read, so loadout cannot tell whether a stealth source writes "
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
            "a stealth source writes personal files here; adopting would sweep them into the team's "
            f"source — remove this repository from stealth source {holder} first: narrow its `targets`, "
            f"then run `loadout sync --stealth {holder}`"
        )


def _removal_target(name: str) -> tuple[RegisteredStealth, bool]:
    registered = load_registry(stealth_path())
    found = next((stealth for stealth in registered if stealth.name == name), None)
    if found is not None:
        return found, True
    scan = scan_stealth_records()
    folders = {stealth.folder for stealth in registered}
    record = next((r for r in _unregistered(registered, scan.records) if r.name == name), None)
    if record is not None:
        return RegisteredStealth(name, record.folder), False
    problem = next(
        (
            p
            for p in scan.problems
            if p.name == name and p.folder is not None and p.folder not in folders
        ),
        None,
    )
    if problem is not None and problem.folder is not None:
        return RegisteredStealth(name, problem.folder), False
    raise LoadoutError(
        f"no stealth source or stealth record named {name}; {_known_names(registered)}; "
        "see `loadout stealth list`"
    )


def _recorded_count(record: StealthRecord) -> int:
    return len(record.shared) + sum(
        len(entry.files)
        + len(entry.documents)
        + sum(len(leaves) for leaves in entry.leaves.values())
        for entry in record.targets.values()
    )


def _confirmed(name: str, count: int, yes: bool) -> bool:
    if yes:
        return True
    if not interactive():
        raise UsageError(f"stealth remove {name} needs --yes when input is not interactive")
    noun = "output" if count == 1 else "outputs"
    try:
        answer = input(
            f"remove stealth source {name} and retire its {count} recorded {noun}? [y/N] "
        )
    except (EOFError, OSError) as error:
        raise UsageError(
            f"stealth remove {name} needs --yes when input is not interactive"
        ) from error
    return answer.strip().lower() in _YES


def _finisher(stealth: RegisteredStealth, registered: bool) -> Callable[[], None]:
    def finish() -> None:
        for path in record_files(stealth.folder):
            path.unlink(missing_ok=True)
        if registered:
            update_registry(lambda current: without_stealth(current, stealth.name))

    return finish


def _left(stealth: RegisteredStealth, registered: bool) -> tuple[str, ...]:
    try:
        registration = registered and any(
            found.name == stealth.name for found in load_registry(stealth_path())
        )
    except (LoadoutError, OSError):
        registration = registered
    try:
        record = bool(record_files(stealth.folder))
    except OSError:
        record = True
    return (*(["registration"] if registration else []), *(["record"] if record else []))


def _failed_removal(stealth: RegisteredStealth, registered: bool) -> str:
    left = _left(stealth, registered)
    if not left:
        return (
            "removed — its registration and record are gone, but the step above failed, so the "
            "global gitignore block may still list its files until another stealth source syncs"
        )
    kept = " and ".join(left)
    verb = "is" if len(left) == 1 else "are"
    return (
        f"not removed; the {kept} {verb} kept — fix the problems above, then run "
        f"`loadout stealth remove {stealth.name}` again"
    )


def cmd_stealth_remove(name: str, *, force: bool, yes: bool) -> int:
    refuse_overlay_registry()
    stealth, registered = _removal_target(name)
    record, status = read_stealth_record(stealth.folder, name, harness_homes().home)
    path = stealth_record_path(stealth.folder)
    if status == "newer":
        raise LoadoutError(f"the stealth record {path} {NEWER_RECORD}")
    if status == "unreadable" and not force:
        raise LoadoutError(
            f"the stealth record {path} cannot be read; fix it, or run "
            f"`loadout stealth remove {name} --force` to treat it as empty"
        )
    if not _confirmed(name, _recorded_count(record), yes):
        print("declined; no changes made")
        return 0
    if registered:
        update_registry(lambda current: marked_removing(current, name))
    clones = adopted_clones((stealth,))
    reached = (*clones.adopted, *clones.stranded)
    deferred = bool(reached) or bool(clones.held) or not clones.listed
    finish = _finisher(stealth, registered)
    run = StealthRun(
        stealth=replace(stealth, removing=True),
        scope=None,
        force=force,
        interactive=False,
        retire_all=True,
        finish=finish if not deferred else lambda: None,
    )
    outcome = sync_stealth(run)
    report(outcome)
    code = max(
        outcome.code,
        _retire_stranded(clones),
        sync_plain(clones.adopted, retiring=frozenset({name})),
    )
    finished = (
        not outcome.code and clones.listed and not clones.held and not _still_naming(name, reached)
    )
    if finished and deferred:
        try:
            with stealth_lock(stealth.folder):
                finish()
        except (LoadoutError, OSError) as error:
            print(f"stealth {name}: ERROR: {error}", file=sys.stderr)
            finished, code = False, 3
    if not finished:
        print(f"stealth {name}: {_failed_removal(stealth, registered)}", file=sys.stderr)
        return code or 3
    print(f"removed stealth source {name}")
    return code


def _still_naming(name: str, clones: Sequence[Path]) -> bool:
    return any(_names(root, name) for root in clones)


def _names(root: Path, name: str) -> bool:
    try:
        return record_names_stealth(root, name)
    except (OSError, ValueError) as error:
        print(f"WARNING: cannot read what loadout recorded for {root}: {error}", file=sys.stderr)
        return True
