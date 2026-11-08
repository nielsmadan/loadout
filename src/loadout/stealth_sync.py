from __future__ import annotations

import functools
import json
import os
import stat
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import stealth_seams
from .atomic import replace_atomically
from .claude_json import (
    LeafResult,
    LeafVerdict,
    claude_json_path,
    escape_surrogates,
    leaf_refusal,
    server_path,
    write_leaves,
)
from .containment import no_symlinks
from .destinations import resolve_destination
from .diffs import report_diff
from .errors import LoadoutError
from .exclude import (
    PERSONAL_UNITS,
    GlobalIgnoreState,
    exclude_path,
    global_pattern,
    offer_text,
    read_global_state,
    user_covered,
    write_global_block,
    write_global_state,
    write_repo_block,
)
from .files import Drift, process_umask, text_mode, write_outputs
from .json_paths import JsonPath, leaf_digest
from .native_documents import jsonc_to_json
from .overlay_leftovers import check_exclude
from .project import project_config_path
from .retirement import retire
from .stealth_documents import OwnedJson
from .stealth_git import (
    covered_by,
    excludes_file,
    reincluded,
    scratch_repo,
    tracked_units,
    unignored,
)
from .stealth_lock import same_path, stealth_lock
from .stealth_manifest import StealthManifest, load_stealth_manifest
from .stealth_plan import (
    DiskFacts,
    GitFacts,
    RecordedAction,
    Refusal,
    TargetPlan,
    classify_recorded,
    folder_units,
    force_command,
    kept,
    plan_target,
    plan_units,
    shared_drift,
    tracked_note,
    tracked_refusal,
    unit_of,
)
from .stealth_record import (
    NEWER_RECORD,
    NO_CLAIMS,
    ForeignClaims,
    RecordStatus,
    StealthRecord,
    TargetRecord,
    UnreadableRecords,
    claimed_leaves,
    claimed_paths,
    claimed_skills,
    empty_record,
    every_stealth_record,
    foreign_claims,
    read_stealth_record,
    record_problem_message,
    scan_stealth_records,
    settled_leaves,
    stealth_record_path,
    with_pending,
    with_target,
    without_target,
    write_stealth_record,
)
from .stealth_registry import RegisteredStealth
from .stealth_render import StealthRender, gather_facts, render_stealth
from .stealth_targets import (
    Expansion,
    HarnessHomes,
    RecordedFate,
    SkippedTarget,
    Target,
    expand_targets,
    harness_homes,
    probe,
    recorded_fate,
    refuse_unsafe_outputs,
)
from .sync import unaccepted_against_record
from .written import WrittenEntry, text_entry

EMPTY_EXPANSION = Expansion(
    targets=(), skipped=(), matched=frozenset(), unlisted=(), unlisted_under=()
)
_EMPTY_DISK = DiskFacts(documents={}, document_bytes={}, claimed={}, claimed_leaves={})
_NO_GIT = GitFacts(tracked=frozenset(), covered=frozenset(), branches={})
_YES = frozenset({"y", "yes"})
ADOPTED_NOTE = "adopted loadout; the stealth source retired its files here"
ADOPTED_SKIP = (
    "adopted loadout (it has loadout/config.toml); the stealth source does not write here"
)

_USER_SERVERS: tuple[tuple[str, str, str], ...] = (
    ("codex", "${CODEX_HOME:-~/.codex}/config.toml", "mcp_servers"),
    ("cursor", "~/.cursor/mcp.json", "mcpServers"),
    ("droid", "${FACTORY_HOME_OVERRIDE:-~}/.factory/mcp.json", "mcpServers"),
    ("opencode", "${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json", "mcp"),
    ("opencode", "${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.jsonc", "mcp"),
    ("pi", "${PI_CODING_AGENT_DIR:-~/.pi/agent}/mcp.json", "mcpServers"),
)


@dataclass(frozen=True)
class StealthRun:
    stealth: RegisteredStealth
    scope: Path | None
    force: bool
    interactive: bool
    retire_all: bool = False
    finish: Callable[[], None] = lambda: None


@dataclass(frozen=True)
class StealthOutcome:
    name: str
    plans: tuple[TargetPlan, ...]
    skipped: tuple[SkippedTarget, ...]
    errors: tuple[str, ...]
    leaves: tuple[tuple[Path, LeafResult], ...]
    code: int
    notes: tuple[str, ...] = ()
    folder: Path | None = None
    shared_written: tuple[Path, ...] = ()
    shared_retired: tuple[Path, ...] = ()


class _Changed(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(str(path))
        self.path = path


class _Failed(Exception):
    def __init__(
        self, record: StealthRecord, error: Exception, entry: TargetRecord | None = None
    ) -> None:
        super().__init__(str(error))
        self.record = record
        self.error = error
        self.entry = entry


@dataclass(frozen=True)
class _Planned:
    target: Target
    render: StealthRender | None
    recorded: TargetRecord | None
    disk: DiskFacts
    gone: bool
    plan: TargetPlan


@dataclass(frozen=True)
class _Context:
    stealth: RegisteredStealth
    force: bool
    manifest: StealthManifest | None
    covered: Callable[[Path, tuple[str, ...], frozenset[str]], frozenset[str]]
    retire_all: bool = False
    claims: Callable[[], ForeignClaims] = lambda: NO_CLAIMS


@dataclass(frozen=True)
class _Prepared:
    record: StealthRecord
    status: RecordStatus
    expansion: Expansion
    actions: dict[Path, RecordedAction]
    planned: tuple[_Planned, ...]
    errors: tuple[str, ...]
    context: _Context | None


@dataclass(frozen=True)
class _Shared:
    writes: Mapping[Path, str]
    retires: Mapping[Path, WrittenEntry]
    entries: Mapping[Path, WrittenEntry]


@dataclass(frozen=True)
class PreparedRun:
    record: StealthRecord
    expansion: Expansion
    plans: tuple[TargetPlan, ...]
    errors: tuple[str, ...]
    shared: tuple[Drift, ...]
    manifest: StealthManifest | None = None


@dataclass(frozen=True)
class LeafWork:
    wanted: dict[JsonPath, Any]
    owned: dict[JsonPath, frozenset[str]]
    retiring: tuple[JsonPath, ...]
    pending: dict[JsonPath, str]


@dataclass(frozen=True)
class _Leaves:
    record: StealthRecord
    results: tuple[tuple[Path, LeafResult], ...]
    errors: tuple[str, ...]


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _parsed(raw: bytes) -> dict[str, Any] | None:
    try:
        document = json.loads(raw)
    except ValueError:
        return None
    return document if isinstance(document, dict) else None


def _output_paths(render: StealthRender | None, recorded: TargetRecord | None) -> tuple[Path, ...]:
    rendered = tuple(render.files) if render is not None else ()
    held = (*recorded.files, *recorded.documents) if recorded is not None else ()
    return tuple(dict.fromkeys([*rendered, *held]))


def _document_paths(
    render: StealthRender | None, recorded: TargetRecord | None
) -> tuple[Path, ...]:
    rendered = render.files if render is not None else {}
    owned = (path for path, output in rendered.items() if isinstance(output, OwnedJson))
    orphans = (path for path in (recorded.documents if recorded else {}) if path not in rendered)
    return tuple(dict.fromkeys([*owned, *orphans]))


def _folders(root: Path, units: Sequence[str], paths: Sequence[Path]) -> frozenset[str]:
    return folder_units(root, paths) & frozenset(units)


def _render(context: _Context, target: Target) -> StealthRender:
    if context.manifest is None:
        raise LoadoutError(f"stealth {context.stealth.name}: no manifest to render from")
    facts = gather_facts(target.root)
    return render_stealth(context.manifest, context.stealth.name, target.root, facts)


def _unparsable(target: Target, recorded: TargetRecord | None, path: Path) -> _Planned:
    refusal = Refusal(
        "unparsable",
        3,
        f"{_rel(target.root, path)} is not a JSON object loadout can merge into; fix the JSON "
        "by hand, then sync again",
        path,
    )
    return _Planned(
        target, None, recorded, _EMPTY_DISK, False, kept(target.root, recorded, refusal)
    )


def _claims(
    context: _Context, root: Path, render: StealthRender | None, paths: tuple[Path, ...]
) -> tuple[dict[Path, str], dict[JsonPath, str]]:
    units = plan_units(root, render, None)
    skills = tuple(root / unit for unit in _folders(root, units, paths))
    servers = render.claude_servers if render is not None else {}
    if not paths and not servers:
        return {}, {}
    claims = context.claims()
    claimed = {**claimed_skills(skills, claims), **claimed_paths(paths, claims)}
    if not servers:
        return claimed, {}
    try:
        file = claude_json_path()
    except LoadoutError:
        return claimed, {}
    leaves = claimed_leaves(((file, server_path(root, name)) for name in servers), claims)
    return claimed, {path: owner for (_, path), owner in leaves.items()}


def _planned(
    context: _Context, target: Target, render: StealthRender | None, recorded: TargetRecord | None
) -> _Planned:
    root = target.root
    paths = _output_paths(render, recorded)
    refuse_unsafe_outputs(target, paths, tuple(render.files) if render is not None else ())
    raw = {path: _read_bytes(path) for path in _document_paths(render, recorded)}
    documents: dict[Path, dict[str, Any] | None] = {}
    for path, data in raw.items():
        documents[path] = None if data is None else _parsed(data)
        if data is not None and documents[path] is None:
            return _unparsable(target, recorded, path)
    units = plan_units(root, render, recorded)
    folders = _folders(root, units, paths)
    git = GitFacts(
        tracked=tracked_units(root, units),
        covered=context.covered(excludes_file(root), units, folders),
        branches={},
        reincluded=reincluded(root, units, folders),
    )
    claimed, leaves = _claims(context, root, render, paths)
    disk = DiskFacts(
        documents=documents, document_bytes=raw, claimed=claimed, claimed_leaves=leaves
    )
    plan = plan_target(
        target,
        render,
        recorded,
        git,
        disk,
        name=context.stealth.name,
        force=context.force,
        retire_all=context.retire_all,
    )
    return _Planned(target, render, recorded, disk, False, plan)


def _plan_isolated(
    context: _Context, target: Target, recorded: TargetRecord | None, *, rendering: bool
) -> _Planned:
    try:
        check_exclude(exclude_path(target.common_dir))
        render = _render(context, target) if rendering else None
        return _planned(context, target, render, recorded)
    except UnreadableRecords:
        raise
    except (LoadoutError, OSError) as error:
        refusal = Refusal("error", 3, str(error))
        plan = kept(target.root, recorded, refusal)
        return _Planned(target, None, recorded, _EMPTY_DISK, False, plan)


def _recorded_target(root: Path, entry: TargetRecord) -> Target:
    return Target(root=root, common_dir=entry.common_dir, device=entry.device)


def _gone(context: _Context, root: Path, entry: TargetRecord) -> _Planned:
    target = _recorded_target(root, entry)
    absent: dict[Path, dict[str, Any] | None] = dict.fromkeys(entry.documents)
    disk = DiskFacts(
        documents=absent,
        document_bytes=dict.fromkeys(entry.documents),
        claimed={},
        claimed_leaves={},
    )
    try:
        plan = plan_target(
            target,
            None,
            entry,
            _NO_GIT,
            disk,
            name=context.stealth.name,
            force=context.force,
            retire_all=context.retire_all,
        )
    except (LoadoutError, OSError) as error:
        plan = kept(root, entry, Refusal("error", 3, str(error)))
    return _Planned(target, None, entry, disk, True, replace(plan, fate="gone"))


def _retired(
    context: _Context, root: Path, entry: TargetRecord, healthy: Target | None
) -> _Planned:
    if healthy is not None:
        return _plan_isolated(context, healthy, entry, rendering=False)
    return _gone(context, root, entry)


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        status = os.stat(path)
    except OSError:
        return None
    return (status.st_dev, status.st_ino)


def _respelled(expansion: Expansion, record: StealthRecord) -> Expansion:
    known = {key: root for root in record.targets if (key := _identity(root)) is not None}

    def spelled(path: Path) -> Path:
        if path in record.targets:
            return path
        key = _identity(path)
        return known.get(key, path) if key is not None else path

    return replace(
        expansion,
        targets=tuple(replace(target, root=spelled(target.root)) for target in expansion.targets),
        skipped=tuple(replace(skip, folder=spelled(skip.folder)) for skip in expansion.skipped),
        matched=frozenset(spelled(path) for path in expansion.matched),
    )


def _healthy(root: Path, homes: HarnessHomes, folder: Path) -> Target | None:
    try:
        found = probe(root, homes, folder)
    except (LoadoutError, OSError):
        return None
    return replace(found, root=root) if isinstance(found, Target) else None


def _unmatched(
    record: StealthRecord, expansion: Expansion, homes: HarnessHomes, folder: Path
) -> tuple[dict[Path, RecordedFate], dict[Path, Target]]:
    fates = {
        root: recorded_fate(root, entry.device)
        for root, entry in record.targets.items()
        if root not in expansion.matched
    }
    healthy = {
        root: target
        for root, fate in fates.items()
        if fate == "present" and (target := _healthy(root, homes, folder)) is not None
    }
    return {
        root: fate for root, fate in fates.items() if fate == "gone" or root in healthy
    }, healthy


def _unexamined(
    record: StealthRecord, expansion: Expansion, fates: Mapping[Path, RecordedFate]
) -> tuple[SkippedTarget, ...]:
    def reason(root: Path, entry: TargetRecord) -> str:
        fate = recorded_fate(root, entry.device)
        why = "cannot be reached" if fate == "unreachable" else "does not probe as a checkout"
        return f"no longer matched by `targets`, but it {why}; its outputs are kept"

    return tuple(
        SkippedTarget(folder=root, kind="unexamined", reason=reason(root, entry))
        for root, entry in record.targets.items()
        if root not in expansion.matched
        and root not in fates
        and not any(root.is_relative_to(parent) for parent in expansion.unlisted_under)
    )


def _left_behind(root: Path, entry: TargetRecord, fate: str) -> _Planned:
    reason = "cannot be reached" if fate == "unreachable" else "does not probe as a checkout"
    outputs = ", ".join(sorted(_rel(root, path) for path in (*entry.files, *entry.documents)))
    note = f"{reason}; --force drops it from the record and leaves its outputs in place" + (
        f": {outputs}" if outputs else ""
    )
    leaves = {
        file: tuple(dict.fromkeys([*entry.leaves.get(file, {}), *entry.pending.get(file, {})]))
        for file in dict.fromkeys([*entry.leaves, *entry.pending])
    }
    plan = replace(
        kept(root, entry, Refusal("error", 3, note)),
        whole=False,
        record=replace(entry, files={}, documents={}),
        retiring_leaves=leaves,
        refusals=(),
        notes=(note,),
        code=0,
        fate="left",
    )
    return _Planned(_recorded_target(root, entry), None, entry, _EMPTY_DISK, True, plan)


def _retire_everything(
    context: _Context, record: StealthRecord, homes: HarnessHomes
) -> tuple[dict[Path, RecordedAction], tuple[_Planned, ...], tuple[str, ...]]:
    folder = context.stealth.folder
    fates = {root: recorded_fate(root, entry.device) for root, entry in record.targets.items()}
    healthy = {
        root: target
        for root, fate in fates.items()
        if fate == "present" and (target := _healthy(root, homes, folder)) is not None
    }
    retiring = [root for root, fate in fates.items() if fate == "gone" or root in healthy]
    stuck = {root: fate for root, fate in fates.items() if root not in retiring}
    planned = (
        *(_retired(context, root, record.targets[root], healthy.get(root)) for root in retiring),
        *(
            _left_behind(root, record.targets[root], fate)
            for root, fate in stuck.items()
            if context.force
        ),
    )
    errors = tuple(
        f"{shown(root)}: "
        + ("cannot be reached" if fate == "unreachable" else "does not probe as a checkout")
        + "; its outputs and record entry are kept — "
        f"`loadout stealth remove {context.stealth.name} --force` drops it from the record"
        for root, fate in stuck.items()
        if not context.force
    )
    actions: dict[Path, RecordedAction] = {
        root: "retire" if root in retiring or context.force else "keep" for root in record.targets
    }
    return actions, _sorted(planned), errors


def _sorted(planned: Sequence[_Planned]) -> tuple[_Planned, ...]:
    return tuple(sorted(planned, key=lambda item: str(item.plan.root)))


def _user_files(harnesses: frozenset[str], environ: Mapping[str, str]) -> list[tuple[Path, str]]:
    files = [
        (resolve_destination(template, f"{harness} user config", environ), key)
        for harness, template, key in _USER_SERVERS
        if harness in harnesses
    ]
    custom = environ.get("OPENCODE_CONFIG")
    return (
        [*files, (Path(custom).expanduser(), "mcp")]
        if custom and "opencode" in harnesses
        else files
    )


def _user_servers(path: Path, key: str) -> frozenset[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return frozenset()
    except (OSError, UnicodeDecodeError) as error:
        raise LoadoutError(f"cannot read {path}: {error}") from None
    try:
        document = (
            tomllib.loads(text)
            if path.suffix == ".toml"
            else json.loads(jsonc_to_json(text) if path.suffix == ".jsonc" else text)
        )
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise LoadoutError(
            f"{path} cannot be parsed ({error}); loadout reads it for MCP servers the stealth source "
            "also defines — fix it, then sync again"
        ) from None
    table = document.get(key) if isinstance(document, dict) else None
    return frozenset(table) if isinstance(table, dict) else frozenset()


def _collisions(manifest: StealthManifest) -> tuple[str, ...]:
    servers = manifest.servers
    if not servers:
        return ()
    harnesses = frozenset(
        target.harness
        for target in manifest.manifest.permissions
        if target.harness is not None and target.name == f"{target.harness}-mcp"
    )
    problems: list[str] = []
    for path, key in _user_files(harnesses, os.environ):
        try:
            names = _user_servers(path, key)
        except LoadoutError as error:
            problems.append(str(error))
            continue
        problems.extend(
            f"{path} already defines the MCP server {name}, which the stealth source also defines; "
            "rename one of them"
            for name in servers
            if name in names
        )
    return tuple(problems)


def expansion_notes(expansion: Expansion) -> tuple[str, ...]:
    return tuple(
        f"the pattern {pattern} cannot be listed, so its targets are neither written nor retired"
        for pattern in expansion.unlisted
    )


def _record_problem(run: StealthRun, status: RecordStatus) -> str | None:
    path = stealth_record_path(run.stealth.folder)
    if status == "newer":
        return f"the stealth record {path} {NEWER_RECORD}"
    if status == "unreadable" and not run.force:
        command = "stealth remove" if run.retire_all else "sync --stealth"
        return (
            f"the stealth record {path} cannot be read; fix it, or run "
            f"`loadout {command} {run.stealth.name} --force` to treat it as empty"
        )
    return None


def _covering(scratch: Path) -> Callable[[Path, tuple[str, ...], frozenset[str]], frozenset[str]]:
    @functools.cache
    def covered(excludes: Path, units: tuple[str, ...], folders: frozenset[str]) -> frozenset[str]:
        return covered_by(scratch, excludes, units, folders)

    return covered


def _failed(record: StealthRecord, status: RecordStatus, *errors: str) -> _Prepared:
    return _Prepared(record, status, EMPTY_EXPANSION, {}, (), errors, None)


def _adopted(expansion: Expansion, record: StealthRecord) -> tuple[Expansion, dict[Path, Target]]:
    adopted = {
        target.root: target
        for target in expansion.targets
        if os.path.isfile(project_config_path(target.root))
    }
    if not adopted:
        return expansion, {}
    skips = tuple(
        SkippedTarget(folder=root, kind="adopted", reason=ADOPTED_SKIP)
        for root in adopted
        if root not in record.targets
    )
    return replace(
        expansion,
        targets=tuple(target for target in expansion.targets if target.root not in adopted),
        skipped=(*expansion.skipped, *skips),
    ), {root: target for root, target in adopted.items() if root in record.targets}


def _noted(item: _Planned, note: str) -> _Planned:
    return replace(item, plan=replace(item.plan, notes=(*item.plan.notes, note)))


def _gathered(
    run: StealthRun,
    context: _Context,
    homes: HarnessHomes,
    record: StealthRecord,
    status: RecordStatus,
) -> _Prepared:
    if run.retire_all:
        actions, planned, errors = _retire_everything(context, record, homes)
        return _Prepared(record, status, EMPTY_EXPANSION, actions, planned, errors, context)
    folder = run.stealth.folder
    manifest = load_stealth_manifest(folder)
    if collisions := _collisions(manifest):
        return _failed(record, status, *collisions)
    context = replace(context, manifest=manifest)
    expanded = expand_targets(manifest.settings, homes, folder, only=run.scope)
    expansion, adopted = _adopted(_respelled(expanded, record), record)
    fates, healthy = _unmatched(record, expansion, homes, folder) if run.scope is None else ({}, {})
    if run.scope is None:
        expansion = replace(
            expansion, skipped=(*expansion.skipped, *_unexamined(record, expansion, fates))
        )
    actions, refusals = classify_recorded(
        record, expansion, fates, scope=run.scope, home=homes.home, force=run.force
    )
    actions = {**actions, **dict.fromkeys(adopted, "retire")}
    rendered = [
        _plan_isolated(context, target, record.targets.get(target.root), rendering=True)
        for target in expansion.targets
    ]
    retired = [
        _retired(context, root, record.targets[root], healthy.get(root))
        for root, action in actions.items()
        if action == "retire" and root not in adopted
    ]
    retired += [
        _noted(_retired(context, root, record.targets[root], target), ADOPTED_NOTE)
        for root, target in adopted.items()
    ]
    errors = tuple(refusal.message for refusal in refusals)
    return _Prepared(
        record, status, expansion, actions, _sorted([*rendered, *retired]), errors, context
    )


def _prepare(run: StealthRun, scratch: Path) -> _Prepared:
    stealth = run.stealth
    if stealth.removing and not run.retire_all:
        empty = empty_record(stealth.folder, stealth.name, Path.home())
        return _failed(
            empty, "absent", f"being removed; run `loadout stealth remove {stealth.name}`"
        )
    try:
        homes = harness_homes()
        record, status = read_stealth_record(stealth.folder, stealth.name, homes.home)
    except (LoadoutError, OSError) as error:
        return _failed(
            empty_record(stealth.folder, stealth.name, Path.home()), "absent", str(error)
        )
    if (problem := _record_problem(run, status)) is not None:
        return _failed(record, status, problem)
    claims = functools.cache(
        functools.partial(foreign_claims, stealth.folder, force=run.force or run.retire_all)
    )
    context = _Context(stealth, run.force, None, _covering(scratch), run.retire_all, claims)
    try:
        return _gathered(run, context, homes, record, status)
    except UnreadableRecords as error:
        return _failed(record, status, *(record_problem_message(p) for p in error.problems))
    except (LoadoutError, OSError) as error:
        return _failed(record, status, str(error))


def prepare(run: StealthRun) -> PreparedRun:
    try:
        with scratch_repo() as scratch:
            prepared = _prepare(run, scratch)
    except (LoadoutError, OSError) as error:
        empty = empty_record(run.stealth.folder, run.stealth.name, Path.home())
        prepared = _failed(empty, "absent", str(error))
    shared, shared_errors = _shared_check(prepared, run)
    return PreparedRun(
        record=prepared.record,
        expansion=prepared.expansion,
        plans=tuple(item.plan for item in prepared.planned),
        errors=(*prepared.errors, *shared_errors),
        shared=shared,
        manifest=prepared.context.manifest if prepared.context is not None else None,
    )


def _store(before: StealthRecord, after: StealthRecord) -> StealthRecord:
    if after != before:
        write_stealth_record(after)
    return after


def _commit_ahead(record: StealthRecord, plan: TargetPlan) -> StealthRecord:
    return _store(record, with_target(record, plan.root, plan.ahead))


def _commit_target(record: StealthRecord, root: Path, entry: TargetRecord) -> StealthRecord:
    return _store(record, with_target(record, root, entry))


def _commit_pending(
    record: StealthRecord, plans: Sequence[TargetPlan], file: Path
) -> StealthRecord:
    updated = leaves_respelled(record, file)
    for plan in plans:
        if plan.wanted_leaves and plan.root in updated.targets:
            digests = {path: leaf_digest(value) for path, value in plan.wanted_leaves.items()}
            entry = with_pending(updated.targets[plan.root], file, digests)
            updated = with_target(updated, plan.root, entry)
    return _store(record, updated)


def _empty(entry: TargetRecord) -> bool:
    return not (entry.files or entry.documents or entry.leaves or entry.pending)


def _without_empty(record: StealthRecord, retired: frozenset[Path]) -> StealthRecord:
    updated = record
    for root in sorted(retired):
        if root in updated.targets and _empty(updated.targets[root]):
            updated = without_target(updated, root)
    return updated


def _commit_leaves(
    record: StealthRecord,
    roots: Sequence[Path],
    results: Sequence[tuple[Path, LeafResult]],
    retired: frozenset[Path],
) -> StealthRecord:
    updated = record
    for root in roots:
        entry = updated.targets.get(root)
        if entry is None:
            continue
        for file, result in results:
            entry = settled_leaves(entry, file, result)
        updated = with_target(updated, root, entry)
    return _store(record, _without_empty(updated, retired))


def _mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def _write_document(path: Path, text: str, expected: bytes | None) -> None:
    try:
        data = escape_surrogates(text).encode("utf-8")
    except UnicodeEncodeError as error:
        raise LoadoutError(f"{path} cannot be written as UTF-8: {error.reason}") from None
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = text_mode(_mode(path), data, "json", process_umask())

    def unchanged_since_planned() -> None:
        if _read_bytes(path) != expected:
            raise _Changed(path)

    replace_atomically(
        path, lambda handle: handle.write(data), mode=mode, before_rename=unchanged_since_planned
    )


def _delete(
    paths: Sequence[Path], entry: TargetRecord, expected: Mapping[Path, bytes | None]
) -> list[Path]:
    files = {path: entry.files[path] for path in paths if path in entry.files}
    retired = retire(files, entry.files)
    documents = [path for path in paths if path not in entry.files and path.is_file()]
    for path in documents:
        if _read_bytes(path) != expected.get(path):
            raise _Changed(path)
        path.unlink()
    return [*retired, *documents]


def _final_entry(
    entry: TargetRecord, before: TargetRecord | None, untouched: frozenset[Path]
) -> TargetRecord:
    old_files = before.files if before is not None else {}
    old_documents = before.documents if before is not None else {}
    return replace(
        entry,
        files={
            **{path: found for path, found in entry.files.items() if path not in untouched},
            **{path: old_files[path] for path in untouched if path in old_files},
        },
        documents={
            **{path: found for path, found in entry.documents.items() if path not in untouched},
            **{path: old_documents[path] for path in untouched if path in old_documents},
        },
    )


def _dropped(
    root: Path,
    units: Mapping[Path, str],
    loose: Mapping[str, str],
    tracked: frozenset[str],
) -> tuple[Refusal, ...]:
    def because(unit: str) -> Refusal:
        if unit in tracked:
            return tracked_refusal(root, unit)
        source = loose[unit]
        reason = f"{source} re-includes it" if source else "nothing ignores it"
        return Refusal("unignored", 3, f"{unit} would show in git status: {reason}", root / unit)

    return tuple(
        because(unit) for unit in dict.fromkeys(units.values()) if unit in loose or unit in tracked
    )


def _progress(
    record: StealthRecord, plan: TargetPlan, before: TargetRecord | None, done: frozenset[Path]
) -> tuple[StealthRecord, TargetRecord]:
    pending = frozenset({*plan.writes, *plan.deletes}) - done
    entry = replace(_final_entry(plan.record, before, pending), block=plan.ahead.block)
    try:
        return _commit_target(record, plan.root, entry), entry
    except LoadoutError:
        return record, entry


def _apply_steps(
    context: _Context,
    record: StealthRecord,
    item: _Planned,
    before: TargetRecord | None,
) -> tuple[StealthRecord, TargetPlan]:
    plan, root, name = item.plan, item.plan.root, context.stealth.name
    common = plan.ahead.common_dir
    done: list[Path] = []
    try:
        write_repo_block(common, name, plan.block_before)
        if before is not None and before.block and before.block != name:
            write_repo_block(common, before.block, ())
        units = plan_units(root, item.render, item.recorded)
        folders = _folders(root, units, _output_paths(item.render, item.recorded))
        writing = {path: unit_of(root, path) for path in plan.writes}
        loose = unignored(root, tuple(dict.fromkeys(writing.values())), folders)
        tracked = tracked_units(
            root, [unit for unit in dict.fromkeys(writing.values()) if unit not in loose]
        )
        refusals = _dropped(root, writing, loose, tracked)
        writes = {
            path: output
            for path, output in plan.writes.items()
            if writing[path] not in loose and writing[path] not in tracked
        }
        documents = {p: o for p, o in writes.items() if p in item.disk.documents}
        whole = {p: o for p, o in writes.items() if p not in documents}
        written: list[Path] = []
        recorded_files = item.recorded.files if item.recorded is not None else {}
        for path, output in whole.items():
            if not context.force and unaccepted_against_record({path: output}, recorded_files, {}):
                raise _Changed(path)
            written.extend(write_outputs({path: output}))
            done.append(path)
        for path, output in documents.items():
            if not isinstance(output, str):
                raise LoadoutError(f"{path}: a shared document renders as text")
            _write_document(path, output, item.disk.document_bytes.get(path))
            done.append(path)
        deleting = {path: unit_of(root, path) for path in plan.deletes}
        held = tracked_units(root, tuple(dict.fromkeys(deleting.values())))
        deletes = tuple(path for path in plan.deletes if deleting[path] not in held)
        retired = _delete(deletes, plan.ahead, item.disk.document_bytes)
        done.extend(deletes)
        write_repo_block(common, name, plan.block_after)
    except (LoadoutError, OSError, _Changed) as error:
        stored, entry = _progress(record, plan, before, frozenset(done))
        raise _Failed(stored, error, entry) from error
    untouched = frozenset(set(plan.writes) - set(writes))
    final = _final_entry(plan.record, before, untouched)
    record = _commit_target(record, root, final)
    notes = tuple(tracked_note(unit) for unit in sorted(held))
    applied = replace(
        plan,
        writes={path: writes[path] for path in (*written, *documents)},
        deletes=tuple(retired),
        record=final,
        refusals=(*plan.refusals, *refusals),
        notes=(*plan.notes, *notes),
        code=max((plan.code, *(refusal.code for refusal in refusals))),
    )
    return record, applied


def _apply_target(
    context: _Context, record: StealthRecord, item: _Planned
) -> tuple[StealthRecord, TargetPlan]:
    plan = item.plan
    try:
        if item.gone:
            committed = _commit_target(record, plan.root, plan.record)
            return committed, replace(plan, writes={}, deletes=())
        ahead = _commit_ahead(record, plan)
    except (LoadoutError, OSError) as error:
        raise _Failed(record, error) from error
    try:
        return _apply_steps(context, ahead, item, item.recorded)
    except (LoadoutError, OSError, _Changed) as error:
        raise _Failed(ahead, error) from error


def _errored(plan: TargetPlan, message: str) -> TargetPlan:
    return replace(
        plan,
        whole=True,
        writes={},
        deletes=(),
        refusals=(*plan.refusals, Refusal("error", 3, message)),
        code=3,
    )


def _failure_message(root: Path, error: Exception) -> str:
    if isinstance(error, _Changed):
        return f"{_rel(root, error.path)} changed while syncing; run sync again"
    return str(error)


def _apply_isolated(
    context: _Context, record: StealthRecord, item: _Planned
) -> tuple[StealthRecord, TargetPlan, bool]:
    try:
        return (*_apply_target(context, record, item), True)
    except _Failed as failed:
        if not isinstance(failed.error, _Changed):
            return failed.record, _errored(item.plan, str(failed.error)), False
        record = failed.record
        recorded = failed.entry if failed.entry is not None else item.recorded
    try:
        fresh = _plan_isolated(context, item.target, recorded, rendering=item.render is not None)
    except UnreadableRecords as error:
        return record, _errored(item.plan, str(error)), False
    if fresh.plan.whole:
        return record, fresh.plan, False
    try:
        return (*_apply_target(context, record, fresh), True)
    except _Failed as failed:
        message = _failure_message(item.plan.root, failed.error)
        return failed.record, _errored(fresh.plan, message), False


def _apply_all(
    context: _Context | None, record: StealthRecord, planned: Sequence[_Planned]
) -> tuple[StealthRecord, tuple[TargetPlan, ...], tuple[Path, ...]]:
    plans: list[TargetPlan] = []
    applied: list[Path] = []
    for item in planned:
        if item.plan.whole or context is None:
            plans.append(item.plan)
            continue
        record, plan, done = _apply_isolated(context, record, item)
        plans.append(plan)
        if done:
            applied.append(plan.root)
    return record, tuple(plans), tuple(applied)


def _may_retire_shared(prepared: _Prepared, run: StealthRun) -> bool:
    planned = {item.plan.root for item in prepared.planned}
    return (
        run.scope is None
        and all(not item.plan.whole for item in prepared.planned)
        and set(prepared.record.targets) <= planned
    )


def _shared_parts(
    prepared: _Prepared, run: StealthRun
) -> tuple[Mapping[Path, str], Mapping[Path, WrittenEntry], dict[Path, WrittenEntry]] | None:
    applicable = [item for item in prepared.planned if not item.plan.whole]
    if not applicable:
        return None
    wanted = next(
        (
            item.render.shared
            for item in applicable
            if item.render is not None and item.render.shared
        ),
        {},
    )
    recorded = prepared.record.shared
    retiring = (
        {path: entry for path, entry in recorded.items() if path not in wanted}
        if _may_retire_shared(prepared, run)
        else {}
    )
    if not wanted and not retiring:
        return None
    for path in (*wanted, *retiring):
        no_symlinks(path, run.stealth.folder)
    return wanted, recorded, retiring


def _shared_check(
    prepared: _Prepared, run: StealthRun
) -> tuple[tuple[Drift, ...], tuple[str, ...]]:
    try:
        parts = _shared_parts(prepared, run)
        return ((), ()) if parts is None else (shared_drift(*parts), ())
    except (LoadoutError, OSError) as error:
        return (), (str(error),)


def _shared_change(prepared: _Prepared, run: StealthRun) -> _Shared | tuple[str, ...] | None:
    parts = _shared_parts(prepared, run)
    if parts is None:
        return None
    wanted, recorded, retiring = parts
    findings = unaccepted_against_record(wanted, recorded, retiring)
    if findings and not run.force:
        return tuple(
            f"{found.path} was changed outside loadout; nothing in the stealth folder was "
            f"written — restore it, or run "
            f"{force_command(run.stealth.name, retire_all=run.retire_all)}"
            for found in findings
        )
    entries = {**recorded, **{path: text_entry(path, text) for path, text in wanted.items()}}
    return _Shared(wanted, retiring, entries)


def _shared_phase(
    prepared: _Prepared, run: StealthRun, record: StealthRecord
) -> tuple[StealthRecord, _Shared | None, tuple[Path, ...], tuple[str, ...], int]:
    try:
        change = _shared_change(prepared, run)
        if change is None:
            return record, None, (), (), 0
        if isinstance(change, tuple):
            return record, None, (), change, 1
        written = tuple(write_outputs(dict(change.writes)))
        stored = _store(record, replace(record, shared=dict(change.entries)))
    except (LoadoutError, OSError) as error:
        return record, None, (), (str(error),), 3
    return stored, change, written, (), 0


def _retire_shared(
    record: StealthRecord, change: _Shared | None, recorded: Sequence[Path], applied: Sequence[Path]
) -> tuple[StealthRecord, tuple[Path, ...], tuple[str, ...]]:
    if change is None or not change.retires or not set(recorded) <= set(applied):
        return record, (), ()
    try:
        retired = tuple(retire(change.retires, change.retires.keys()))
        kept = {path: entry for path, entry in record.shared.items() if path not in change.retires}
        return _store(record, replace(record, shared=kept)), retired, ()
    except (LoadoutError, OSError) as error:
        return record, (), (str(error),)


def _held_leaves(entry: TargetRecord | None) -> bool:
    return entry is not None and bool(entry.leaves or entry.pending)


def _owned(entries: Sequence[TargetRecord], file: Path) -> dict[JsonPath, frozenset[str]]:
    recorded = {
        path: digest for entry in entries for path, digest in entry.leaves.get(file, {}).items()
    }
    pending = {
        path: digest for entry in entries for path, digest in entry.pending.get(file, {}).items()
    }
    return {
        path: frozenset(
            digest
            for digest in (
                recorded.get(path),
                pending.get(path) if path in recorded else None,
            )
            if digest is not None
        )
        for path in dict.fromkeys([*recorded, *pending])
    }


def _moved(
    held: Mapping[Path, Mapping[JsonPath, str]], current: Path
) -> dict[Path, dict[JsonPath, str]]:
    moved: dict[Path, dict[JsonPath, str]] = {}
    for file in sorted(held, key=lambda found: found == current):
        key = current if same_path(file, current) else file
        moved[key] = {**moved.get(key, {}), **held[file]}
    return moved


def leaves_respelled(record: StealthRecord, current: Path) -> StealthRecord:
    return replace(
        record,
        targets={
            root: replace(
                entry, leaves=_moved(entry.leaves, current), pending=_moved(entry.pending, current)
            )
            for root, entry in record.targets.items()
        },
    )


def leaf_work(
    record: StealthRecord, plans: Sequence[TargetPlan], current: Path, home: Path
) -> dict[Path, LeafWork]:
    record = leaves_respelled(record, current)
    entries = [entry for plan in plans if (entry := record.targets.get(plan.root)) is not None]
    files = dict.fromkeys(
        [current, *(file for entry in entries for file in (*entry.leaves, *entry.pending))]
    )
    wanted = {path: value for plan in plans for path, value in plan.wanted_leaves.items()}
    retiring = {
        file: tuple(
            dict.fromkeys(
                [path for plan in plans for path in plan.retiring_leaves.get(file, ())]
                if file == current
                else [
                    path
                    for entry in entries
                    for path in (*entry.leaves.get(file, {}), *entry.pending.get(file, {}))
                ]
                if record.home == home
                else []
            )
        )
        for file in files
    }
    return {
        file: LeafWork(
            wanted=wanted if file == current else {},
            owned=_owned(entries, file),
            retiring=retiring[file],
            pending={
                path: digest
                for entry in entries
                for path, digest in entry.pending.get(file, {}).items()
            },
        )
        for file in files
    }


def _released(
    result: LeafResult, wanted: Mapping[JsonPath, Any], retiring: Sequence[JsonPath]
) -> LeafResult:
    refused = {verdict.path for verdict in result.refused}
    let_go = (path for path in retiring if path not in wanted and path not in refused)
    return replace(result, retired=tuple(dict.fromkeys([*result.retired, *let_go])))


def _leaf_phase(
    record: StealthRecord, plans: Sequence[TargetPlan], retired: frozenset[Path], *, force: bool
) -> _Leaves:
    involved = [
        plan for plan in plans if plan.wanted_leaves or _held_leaves(record.targets.get(plan.root))
    ]
    roots = [plan.root for plan in involved]
    try:
        if not involved:
            return _Leaves(_commit_leaves(record, (), (), retired), (), ())
        current = claude_json_path()
        work = leaf_work(record, involved, current, harness_homes().home)
        record = _commit_pending(record, involved, current)
    except (LoadoutError, OSError) as error:
        return _Leaves(_dropping_empty(record, retired), (), (str(error),))
    results: list[tuple[Path, LeafResult]] = []
    settling: list[tuple[Path, LeafResult]] = []
    errors: list[str] = []
    for file, item in work.items():
        if not item.wanted and not item.retiring:
            continue
        try:
            result = write_leaves(
                file, item.wanted, item.owned, item.retiring, force=force, pending=item.pending
            )
        except (LoadoutError, OSError) as error:
            errors.append(f"{shown(file)}: {error}")
            continue
        results.append((file, result))
        settling.append((file, _released(result, item.wanted, item.retiring)))
    try:
        record = _commit_leaves(record, roots, settling, retired)
    except LoadoutError as error:
        errors.append(str(error))
    return _Leaves(record, tuple(results), tuple(errors))


def _dropping_empty(record: StealthRecord, retired: frozenset[Path]) -> StealthRecord:
    try:
        return _store(record, _without_empty(record, retired))
    except LoadoutError:
        return record


def _leaf_refusal(file: Path, verdict: LeafVerdict, name: str, *, retire_all: bool) -> Refusal:
    if verdict.kind == "user-defined":
        return Refusal("user-defined", 3, leaf_refusal(verdict), file)
    command = force_command(name, retire_all=retire_all)
    if retire_all:
        remedy = "retire it anyway"
    else:
        remedy = "restore it" if verdict.kind == "owned-key-removed" else "replace it"
    return Refusal("guard", 1, f"{leaf_refusal(verdict)}; run {command} to {remedy}", file)


def _with_leaf_refusals(
    plans: Sequence[TargetPlan],
    results: Sequence[tuple[Path, LeafResult]],
    name: str,
    *,
    retire_all: bool,
) -> tuple[TargetPlan, ...]:
    refused = [(file, verdict) for file, result in results for verdict in result.refused]

    def settled(plan: TargetPlan) -> TargetPlan:
        found = tuple(
            _leaf_refusal(file, verdict, name, retire_all=retire_all)
            for file, verdict in refused
            if verdict.path[1] == str(plan.root)
        )
        if not found:
            return plan
        code = max((plan.code, *(refusal.code for refusal in found)))
        return replace(plan, refusals=(*plan.refusals, *found), code=code)

    return tuple(settled(plan) for plan in plans)


def _owned_writes(plan: TargetPlan) -> tuple[Path, ...]:
    documents = plan.record.documents
    return tuple(
        path
        for path in plan.writes
        if path in plan.record.files
        or (path in documents and (documents[path].owned_keys or documents[path].owned_rules))
    )


def _written_personal(plans: Sequence[TargetPlan]) -> frozenset[str]:
    return frozenset(
        relative
        for plan in plans
        for path in _owned_writes(plan)
        if (relative := _rel(plan.root, path)) in PERSONAL_UNITS
    )


def personal_units(records: Sequence[StealthRecord]) -> tuple[str, ...]:
    held = {
        _rel(root, path)
        for record in records
        for root, entry in record.targets.items()
        for path in (*entry.files, *entry.documents)
    }
    return tuple(unit for unit in PERSONAL_UNITS if unit in held)


def _covered_by_lines(scratch: Path, lines: Sequence[str], units: Sequence[str]) -> frozenset[str]:
    with tempfile.TemporaryDirectory(prefix="loadout-stealth-") as folder:
        copy = Path(folder) / "ignore"
        copy.write_text(
            "".join(f"{line}\n" for line in lines), encoding="utf-8", errors="surrogateescape"
        )
        return covered_by(scratch, copy, units, frozenset())


def _write_global(file: Path) -> None:
    with scratch_repo() as scratch:

        def patterns(lines: Sequence[str]) -> Sequence[str]:
            scan = scan_stealth_records()
            if scan.problems:
                raise LoadoutError(
                    f"{file} is left as it is while a stealth record cannot be read: "
                    f"{record_problem_message(scan.problems[0])}"
                )
            personal = personal_units(scan.records)
            covered = _covered_by_lines(scratch, lines, personal)
            return tuple(global_pattern(unit) for unit in personal if unit not in covered)

        write_global_block(file, patterns)


def _offer(
    state: GlobalIgnoreState, personal: Sequence[str], root: Path, ask: Callable[[str], str]
) -> GlobalIgnoreState:
    file = state.file or excludes_file(root)
    with scratch_repo() as scratch:
        covered = user_covered(scratch, file, personal)
    missing = [unit for unit in personal if unit not in covered]
    if not missing:
        return state
    try:
        answer = ask(f"{offer_text(missing, file)} [y/N] ")
    except EOFError:
        return state
    chosen = GlobalIgnoreState("accepted" if answer.strip().lower() in _YES else "declined", file)
    write_global_state(chosen)
    return chosen


def _global_ignore(
    run: StealthRun, plans: Sequence[TargetPlan], ask: Callable[[str], str]
) -> tuple[str, ...]:
    written = _written_personal(plans)
    try:
        state = read_global_state()
        personal = personal_units(every_stealth_record())
        if state.answer == "unasked" and run.interactive and written:
            root = next(
                plan.root
                for plan in plans
                if written & {_rel(plan.root, p) for p in _owned_writes(plan)}
            )
            state = _offer(state, personal, root, ask)
        if state.answer == "accepted" and state.file is not None:
            _write_global(state.file)
    except (LoadoutError, OSError) as error:
        return (f"global gitignore: {error}",)
    return ()


def _locked(run: StealthRun, scratch: Path) -> StealthOutcome:
    prepared = _prepare(run, scratch)
    if prepared.status == "unreadable" and run.force and not prepared.errors:
        print(
            f"stealth {run.stealth.name}: WARNING: the stealth record "
            f"{stealth_record_path(run.stealth.folder)} cannot be read; --force treats it as empty",
            file=sys.stderr,
        )
    record = replace(prepared.record, name=run.stealth.name)
    record, change, shared_written, shared_errors, shared_code = _shared_phase(
        prepared, run, record
    )
    record, plans, applied = _apply_all(prepared.context, record, prepared.planned)
    record, shared_retired, unretired = _retire_shared(
        record, change, tuple(prepared.record.targets), applied
    )
    retired = frozenset(root for root, action in prepared.actions.items() if action == "retire")
    applied_plans = [plan for plan in plans if plan.root in applied]
    leaves = _leaf_phase(record, applied_plans, retired & frozenset(applied), force=run.force)
    plans = _with_leaf_refusals(plans, leaves.results, run.stealth.name, retire_all=run.retire_all)
    errors = (*prepared.errors, *unretired, *leaves.errors)
    code = max(
        (
            0,
            shared_code,
            *(plan.code for plan in plans),
            *(3 for skip in prepared.expansion.skipped if skip.failure),
            *(3 for _ in errors),
        )
    )
    if run.retire_all and code == 0:
        try:
            run.finish()
        except (LoadoutError, OSError) as error:
            errors, code = (*errors, str(error)), 3
    return StealthOutcome(
        name=run.stealth.name,
        plans=plans,
        skipped=prepared.expansion.skipped,
        errors=(*errors, *shared_errors),
        leaves=leaves.results,
        code=code,
        notes=expansion_notes(prepared.expansion),
        folder=run.stealth.folder,
        shared_written=shared_written,
        shared_retired=shared_retired,
    )


def sync_stealth(run: StealthRun, *, ask: Callable[[str], str] = input) -> StealthOutcome:
    try:
        with stealth_lock(run.stealth.folder), scratch_repo() as scratch:
            outcome = _locked(run, scratch)
    except (LoadoutError, OSError) as error:
        return StealthOutcome(run.stealth.name, (), (), (str(error),), (), 3)
    extra = _global_ignore(run, outcome.plans, ask)
    if not extra:
        return outcome
    return replace(outcome, errors=(*outcome.errors, *extra), code=max(outcome.code, 3))


def shown(path: Path) -> str:
    home = Path(os.environ.get("HOME") or Path.home())
    for spelling in dict.fromkeys([home, home.resolve()]):
        if path == spelling:
            return "~"
        if path.is_relative_to(spelling):
            return f"~/{path.relative_to(spelling).as_posix()}"
    return str(path)


def _report_changes(prefix: str, plan: TargetPlan) -> None:
    if plan.fate == "gone":
        print(f"{prefix}retired: the folder is gone")
        return
    if plan.fate == "left":
        return
    owned = {*plan.record.files, *plan.record.documents}
    wrote = _owned_writes(plan)
    stripped = [path for path in plan.writes if path not in wrote]
    for shown_path in sorted(_rel(plan.root, path) for path in wrote):
        print(f"{prefix}wrote {shown_path}")
    for shown_path in sorted(_rel(plan.root, path) for path in (*stripped, *plan.deletes)):
        print(f"{prefix}retired {shown_path}")
    written = len(wrote)
    retired = len(stripped) + len(plan.deletes)
    unchanged = len(owned - set(plan.writes))
    summary = (
        f"{written} written, {unchanged} unchanged, {retired} retired"
        if written or retired or plan.refusals
        else "Up to date."
    )
    print(f"{prefix}{summary}")


def _report_plan(lead: str, plan: TargetPlan) -> tuple[list[str], bool]:
    prefix = f"{lead}: {shown(plan.root)}: "
    sentences = [r for r in plan.refusals if r.kind == "abort"]
    full: list[str] = []
    truncated = False
    for refusal in plan.refusals:
        if refusal in sentences:
            continue
        label = "ERROR" if refusal.kind == "error" else "refused"
        print(f"{prefix}{label}: {refusal.message}", file=sys.stderr)
        if refusal.diff:
            full.extend(refusal.diff)
            truncated = report_diff(list(refusal.diff)) or truncated
    for refusal in sentences:
        print(f"\n{refusal.message}", file=sys.stderr)
    if not plan.whole:
        _report_changes(prefix, plan)
    for note in plan.notes:
        print(f"note: {prefix}{note}")
    return full, truncated


def _servers(count: int) -> str:
    return f"{count} server{'' if count == 1 else 's'}"


def report(outcome: StealthOutcome) -> None:
    lead = f"stealth {outcome.name}"
    for error in outcome.errors:
        print(f"{lead}: ERROR: {error}", file=sys.stderr)
    for skip in outcome.skipped:
        print(f"{lead}: {shown(skip.folder)}: skipped: {skip.reason}", file=sys.stderr)
    for note in outcome.notes:
        print(f"note: {lead}: {note}")
    if outcome.folder is not None:
        for verb, paths in (("wrote", outcome.shared_written), ("retired", outcome.shared_retired)):
            for path in sorted(paths):
                print(f"{lead}: {shown(outcome.folder)}: {verb} {_rel(outcome.folder, path)}")
    full: list[str] = []
    truncated = False
    for plan in outcome.plans:
        lines, cut = _report_plan(lead, plan)
        full.extend(lines)
        truncated = truncated or cut
    for file, result in outcome.leaves:
        if result.written or result.retired:
            print(
                f"{lead}: {shown(file)}: {_servers(len(result.written))} written, "
                f"{len(result.retired)} retired"
            )
    if truncated and (kept_at := stealth_seams.write_full_diff(full)) is not None:
        print(f"\nThe complete diff is at {kept_at}", file=sys.stderr)
