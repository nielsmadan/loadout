"""One kit change, every project that uses it (ADR 0057).

The projects are the roots this machine's sync records name whose config lists a kit. A
sync at the global source updates each of them; a sync in a project, in a terminal,
offers to push its vendored edits to the kit's source and then updates the other
projects that use it. Git hooks call `cmd_sync` directly and never reach this module.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .commands import (
    check_result,
    check_stranded,
    cmd_check,
    cmd_sync,
    retire_personal,
    stranded_result,
)
from .diffs import report_diff
from .errors import LoadoutError
from .kit_push import Held, Level, Push, apply_push, plan_push, record_matching
from .kit_vendor import kit_diff, refresh_directory, vendor_catalog, vendored_state
from .kits import (
    contained,
    kit_divergence,
    record_hash,
    tree_hash,
    upstream_or_none,
    vendored_path,
)
from .machine import resolve_profile
from .personal_sync import NO_REMOVAL, Removal
from .project import load_project_config, project_config_path
from .resolve import ResolvedItem
from .stealth_sync import shown
from .written import recorded_roots

_EDITED = (
    "the vendored copy has edits its source does not have; `loadout sync` in this project, "
    "in a terminal, offers to push them"
)
_UNVERIFIABLE = "the vendored copy has no recorded provenance, so it is not updated"
_UPDATE_NOTES = {"edited": _EDITED, "unverifiable": _UNVERIFIABLE}
_PREVIEWS = {
    "stale": "its source changed; `loadout sync --global` updates it",
    "level": "it matches its source; `loadout sync --global` records its hash",
    "unverifiable": _UNVERIFIABLE,
}


@dataclass(frozen=True)
class Project:
    """A recorded project root and the kits its config lists, or None when the config does
    not load, so its own sync reports why."""

    root: Path
    kits: tuple[str, ...] | None


@dataclass(frozen=True)
class ProjectIndex:
    projects: tuple[Project, ...]
    problem: str | None = None

    @property
    def roots(self) -> tuple[Path, ...]:
        return tuple(project.root for project in self.projects)


def project_index(skip: Path | None = None) -> ProjectIndex:
    """Every project this machine has synced whose config lists a kit, or does not load."""
    try:
        roots = recorded_roots()
    except OSError as error:
        return ProjectIndex((), f"cannot list the projects this machine has synced: {error}")
    projects = (_project(root) for root in roots if root != skip)
    return ProjectIndex(tuple(p for p in projects if p is not None and p.kits != ()))


def _project(root: Path) -> Project | None:
    path = project_config_path(root)
    try:
        return Project(root, load_project_config(path).kits) if path.is_file() else None
    except (LoadoutError, OSError):
        return Project(root, None)


def warn(index: ProjectIndex) -> None:
    if index.problem is not None:
        print(f"WARNING: {index.problem}, so none is updated", file=sys.stderr)


@dataclass
class Upstreams:
    """This run's kit sources, each resolved and hashed once. Only vendored copies change
    while projects are updated, so the memo holds for the length of a run."""

    _found: dict[str, ResolvedItem | None] = field(default_factory=dict)
    _hashes: dict[Path, str] = field(default_factory=dict)

    def found(self, name: str) -> ResolvedItem | None:
        if name not in self._found:
            self._found[name] = upstream_or_none(name)
        return self._found[name]

    def hash(self, path: Path) -> str:
        if path not in self._hashes:
            self._hashes[path] = tree_hash(path)
        return self._hashes[path]


def projects_using(
    index: ProjectIndex, upstreams: Iterable[Path], memo: Upstreams
) -> tuple[Path, ...]:
    """The indexed projects listing a kit that resolves to one of `upstreams`."""
    wanted = set(upstreams)
    return tuple(
        project.root
        for project in index.projects
        if any(
            (found := memo.found(name)) is not None and found.path in wanted
            for name in project.kits or ()
        )
    )


def sync_with_push(
    root: Path, profile: str, *, force: bool, ask: Callable[[str], bool] | None
) -> int:
    """Sync a root; given `ask`, first offer its vendored edits to their sources, and
    afterwards update the other projects using what was pushed, even when the root's own
    sync fails."""
    if ask is None or not project_config_path(root).is_file():
        return cmd_sync(root, profile=profile, force=force)
    index, memo = project_index(skip=root), Upstreams()
    pushed = push_edits(root, index, memo, ask)
    fanned = 0
    try:
        code = cmd_sync(root, profile=profile, force=force)
    finally:
        if pushed:
            warn(index)
            fanned = sync_projects(projects_using(index, pushed, memo))
    return max(code, fanned)


def push_edits(
    root: Path, index: ProjectIndex, memo: Upstreams, ask: Callable[[str], bool]
) -> tuple[Path, ...]:
    """Offer each edited vendored kit's edits to its source, one kit at a time, and return
    the sources the pushes changed. A kit that fails is reported and the rest still run;
    a project loadout may not read is left to the sync that follows to report."""
    try:
        contained(root)
        for name in load_project_config(project_config_path(root)).kits:
            contained(root, name)
        names = kit_divergence(root)
    except (LoadoutError, OSError):
        return ()
    starts = {name: _start(memo, name) for name in names}
    pushed: list[Path] = []
    for name in names:
        try:
            plan = plan_push(root, name, start=starts[name])
            pushed.extend(_push_one(root, plan, index, memo, ask))
        except (LoadoutError, OSError) as error:
            print(f"WARNING: kit {name}: not pushed — {error}", file=sys.stderr)
    return tuple(pushed)


def _start(memo: Upstreams, name: str) -> str | None:
    found = memo.found(name)
    try:
        return None if found is None else tree_hash(found.path)
    except (LoadoutError, OSError):
        return None


def _push_one(
    root: Path,
    plan: Push | Held | Level,
    index: ProjectIndex,
    memo: Upstreams,
    ask: Callable[[str], bool],
) -> tuple[Path, ...]:
    if isinstance(plan, Level):
        record_matching(root)
        return ()
    if isinstance(plan, Held):
        _report_held(root, plan)
        return ()
    if not ask(_offer(plan, index, memo)):
        print(f"not pushed; {plan.name} keeps its edits in this project")
        return ()
    apply_push(root, plan)
    print(
        f"pushed {plan.name} to source {plan.source} ({shown(plan.upstream)}), "
        f"{len(plan.changes)} file(s); commit them there"
    )
    return _touched(plan)


def _touched(push: Push) -> tuple[Path, ...]:
    return (push.upstream, *(push.upstream.parent / f"{name}.toml" for name in push.also))


def _printable(text: str) -> str:
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in text)


def _report_held(root: Path, held: Held) -> None:
    print(f"WARNING: kit {held.name}: edits not pushed — {held.reason}", file=sys.stderr)
    if held.upstream is None:
        return
    report_diff(kit_diff(vendored_path(root, held.name), held.upstream, held.name))
    print(
        f"Each '-' line is in this project's copy and each '+' line in "
        f"{shown(held.upstream)}; either side may have changed since the copy was "
        f"vendored. Bring the source to what you want by hand; once the copy and the "
        f"source match, the next `loadout sync` in a terminal records it.",
        file=sys.stderr,
    )


def _offer(push: Push, index: ProjectIndex, memo: Upstreams) -> str:
    print(f"kit {push.name} has edits its source does not have; '+' lines are the copy's:")
    report_diff(kit_diff(push.upstream, push.local, push.name))
    for path, content in push.changes:
        print(f"    {'remove' if content is None else 'write'} {_printable(shown(path))}")
    if push.also:
        print(f"    which also changes kits {', '.join(push.also)} in source {push.source}")
    others = len(projects_using(index, _touched(push), memo))
    update = f" and update {others} other project(s)" if others else ""
    return f"Push them to source {push.source}{update}? [y/N] "


def sync_projects(roots: Iterable[Path], memo: Upstreams | None = None) -> int:
    """Update each project's vendored kits from their sources, then sync it. A failure is
    reported for its project and the rest still run; the largest code wins."""
    run = memo if memo is not None else Upstreams()
    return max((_each(root, lambda found: _sync_one(found, run)) for root in roots), default=0)


def check_projects(roots: Iterable[Path]) -> int:
    run = Upstreams()
    return max((_each(root, lambda found: _check_one(found, run)) for root in roots), default=0)


def sync_plain(roots: Iterable[Path], *, removal: Removal = NO_REMOVAL) -> int:
    return max(
        (
            _each(root, lambda found: cmd_sync(found, resolve_profile(found), removal=removal))
            for root in roots
        ),
        default=0,
    )


def retire_plain(roots: Iterable[Path]) -> int:
    return max((_each(root, retire_personal) for root in roots), default=0)


def check_retired(roots: Iterable[tuple[Path, str]]) -> int:
    def checked(sync: str) -> Callable[[Path], int]:
        return lambda found: check_stranded(found, sync)

    return max((_each(root, checked(sync)) for root, sync in roots), default=0)


def check_retired_json(roots: Iterable[tuple[Path, str]]) -> tuple[ProjectCheck, ...]:
    return tuple(_retired_json_one(root, sync) for root, sync in roots)


def _retired_json_one(root: Path, sync: str) -> ProjectCheck:
    try:
        result = stranded_result(root, sync)
    except (LoadoutError, OSError) as error:
        return ProjectCheck(root, 3, (), (), str(error))
    return ProjectCheck(
        root, int(result["exit_code"]), tuple(result["drift"]), tuple(result["notes"])
    )


def check_plain(roots: Iterable[Path]) -> int:
    return max(
        (_each(root, lambda found: cmd_check(found, resolve_profile(found))) for root in roots),
        default=0,
    )


def _each(root: Path, action: Callable[[Path], int]) -> int:
    print(f"\nproject {shown(root)}:")
    try:
        return action(root)
    except (LoadoutError, OSError) as error:
        print(f"project {shown(root)}: ERROR: {error}", file=sys.stderr)
        return 3


def _sync_one(root: Path, memo: Upstreams) -> int:
    for note in _update_kits(root, memo):
        print(f"note: project {shown(root)}: {note}")
    return cmd_sync(root, resolve_profile(root))


def _check_one(root: Path, memo: Upstreams) -> int:
    for note in kit_previews(root, memo):
        print(f"note: project {shown(root)}: {note}")
    return cmd_check(root, resolve_profile(root))


def _vendored(root: Path) -> tuple[tuple[str, Path, str | None], ...]:
    """Each vendored kit the project lists: its name, copy and recorded hash, after the
    containment every kit command holds to."""
    contained(root)
    config = load_project_config(project_config_path(root))
    for name in config.kits:
        contained(root, name)
    return tuple(
        (name, local, config.vendored_hash(name))
        for name in config.kits
        if (local := vendored_path(root, name)).exists()
    )


def _update_kits(root: Path, memo: Upstreams) -> tuple[str, ...]:
    notes: list[str] = []
    for name, local, recorded in _vendored(root):
        try:
            note = _update_kit(root, name, local, recorded, memo)
        except LoadoutError as error:
            note = f"not updated: {error}"
        if note is not None:
            notes.append(f"kit {name}: {note}")
    return tuple(notes)


def _comparable(name: str, local: Path, memo: Upstreams) -> ResolvedItem:
    found = memo.found(name)
    if found is None:
        raise LoadoutError("its source does not resolve here")
    if local.is_file() != found.path.is_file():
        raise LoadoutError("the copy and its source are not the same kind of kit")
    return found


def _update_kit(
    root: Path, name: str, local: Path, recorded: str | None, memo: Upstreams
) -> str | None:
    found = _comparable(name, local, memo)
    copy = tree_hash(local)
    state = vendored_state(copy, recorded, memo.hash(found.path))
    if state == "level":
        record_hash(root, name, copy)
        return "it matches its source; recorded its hash"
    if state != "stale":
        return _UPDATE_NOTES.get(state)
    if local.is_file():
        vendor_catalog(root, name, found, update=True, yes=True)
        return "updated from its source; commit loadout/ in this project"
    changed = refresh_directory(root, name, found.path)
    return f"updated from its source, {len(changed)} file(s); commit loadout/ in this project"


def kit_previews(root: Path, memo: Upstreams) -> tuple[str, ...]:
    """What `sync --global` would do to each of the project's vendored kits."""
    notes: list[str] = []
    for name, local, recorded in _vendored(root):
        try:
            found = _comparable(name, local, memo)
        except LoadoutError as error:
            notes.append(f"kit {name}: not updated: {error}")
            continue
        state = vendored_state(tree_hash(local), recorded, memo.hash(found.path))
        if state in _PREVIEWS:
            notes.append(f"kit {name}: {_PREVIEWS[state]}")
    return tuple(notes)


@dataclass(frozen=True)
class ProjectCheck:
    root: Path
    exit_code: int
    drift: tuple[dict[str, Any], ...]
    notes: tuple[str, ...]
    error: str | None = None


def check_projects_json(
    roots: Iterable[Path], *, previews: bool = True
) -> tuple[ProjectCheck, ...]:
    """Each project's check result, with its kit previews among the notes."""
    memo = Upstreams() if previews else None
    return tuple(_check_json_one(root, memo) for root in roots)


def _check_json_one(root: Path, memo: Upstreams | None) -> ProjectCheck:
    try:
        previews = kit_previews(root, memo) if memo is not None else ()
        result = check_result(root, resolve_profile(root))
    except (LoadoutError, OSError) as error:
        return ProjectCheck(root, 3, (), (), str(error))
    return ProjectCheck(
        root, int(result["exit_code"]), tuple(result["drift"]), (*previews, *result["notes"])
    )
