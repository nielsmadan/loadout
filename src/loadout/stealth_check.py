from __future__ import annotations

import json
import os
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from . import notices, stealth_seams
from .claude_json import (
    claude_json_path,
    escape_surrogates,
    judge_leaves,
    leaf_refusal,
    read_claude_document,
)
from .destinations import resolve_destination
from .diffs import diff_lines, report_diff
from .emit import global_rules, global_servers
from .errors import LoadoutError
from .exclude import PERSONAL_UNITS, read_global_state, user_covered
from .files import Drift, DriftKind
from .json_paths import JsonPath, display_path, lookup
from .native_documents import jsonc_to_json
from .runtime_paths import harness_owned
from .stealth_git import (
    branches_tracking,
    excludes_file,
    scratch_repo,
    tracked_files,
    tracked_units,
    units_in,
)
from .stealth_lock import same_path
from .stealth_manifest import StealthManifest, load_stealth_manifest
from .stealth_plan import Refusal, TargetPlan, path_units
from .stealth_record import (
    StealthRecord,
    TargetRecord,
    UnreadableRecords,
    every_stealth_record,
    record_problem_message,
    recorded_paths_within,
)
from .stealth_registry import RegisteredStealth
from .stealth_render import STEALTH_PRESET
from .stealth_sync import (
    StealthRun,
    expansion_notes,
    leaf_work,
    personal_units,
    prepare,
    shown,
)
from .stealth_targets import harness_homes
from .unmanaged import Readers, unmanaged_in
from .written import every_recorded_path

CODEX_DIR = ".codex/"
CODEX_CONFIG = ".codex/config.toml"
OPENCODE_CONFIG = "opencode.json"
TEAM_OPENCODE = ".opencode/opencode.json"
PI_MCP = ".pi/mcp.json"
PI_FILES = (
    ".pi/settings.json",
    ".pi/mcp.json",
    ".pi/SYSTEM.md",
    ".pi/APPEND_SYSTEM.md",
)
PI_PREFIXES = (
    ".pi/extensions/",
    ".pi/skills/",
    ".pi/prompts/",
    ".pi/themes/",
    ".agents/skills/",
)
SKILL_FOLDERS = {
    "claude": ".claude/skills",
    "cursor": ".cursor/skills",
    "opencode": ".opencode/skills",
    "pi": ".pi/skills",
}
SHARED_SKILLS = ".agents/skills"
PI_MCP_NOTE = (
    'Pi\'s "Disable in this project" writes .pi/mcp.json, which the stealth source owns; '
    "make the change in the stealth source"
)


@dataclass(frozen=True)
class StealthCheck:
    name: str
    drift: tuple[Drift, ...]
    notes: tuple[str, ...]
    errors: tuple[tuple[Path | None, str], ...]
    code: int
    json_labels: frozenset[Path] = frozenset()


@dataclass(frozen=True)
class Machine:
    codex_file: Path
    codex: Mapping[str, Any]
    pi_trust_file: Path
    pi_trust: Mapping[str, Any]
    opencode_race: bool


@dataclass(frozen=True)
class TargetView:
    root: Path
    outputs: tuple[str, ...]
    codex_config: str | None
    stealth_opencode: Mapping[str, Any]
    team_opencode: Mapping[str, Any]
    pi_mcp_edited: bool
    branches: Mapping[str, tuple[str, ...]]
    carried: tuple[str, ...]
    render_notes: tuple[str, ...]


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def _toml(path: Path) -> Mapping[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def _json(text: str | None) -> Mapping[str, Any]:
    try:
        document = json.loads(jsonc_to_json(text or ""))
    except (ValueError, LoadoutError):
        return {}
    return document if isinstance(document, dict) else {}


def read_machine(environ: Mapping[str, str]) -> Machine:
    codex_file = resolve_destination("${CODEX_HOME:-~/.codex}/config.toml", "codex user config")
    trust_file = resolve_destination("${PI_CODING_AGENT_DIR:-~/.pi/agent}/trust.json", "pi trust")
    try:
        trust = _json(trust_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        trust = {}
    return Machine(
        codex_file=codex_file,
        codex=_toml(codex_file),
        pi_trust_file=trust_file,
        pi_trust=trust,
        opencode_race=notices.opencode_skills_race(environ),
    )


def codex_trusted(root: Path, codex: Mapping[str, Any]) -> bool:
    projects = codex.get("projects")
    if not isinstance(projects, dict):
        return False
    spellings = {
        str(p)
        for spelled in dict.fromkeys([root, root.resolve()])
        for p in (spelled, *spelled.parents)
    }
    return any(
        isinstance(entry, dict) and entry.get("trust_level") == "trusted"
        for key, entry in projects.items()
        if key in spellings
    )


def pi_decision(root: Path, trust: Mapping[str, Any]) -> bool | None:
    canonical = root.resolve()
    for folder in (canonical, *canonical.parents):
        decision = trust.get(str(folder))
        if isinstance(decision, bool):
            return decision
    return None


def _codex_notes(view: TargetView, machine: Machine) -> tuple[str, ...]:
    found: list[str] = []
    if any(output.startswith(CODEX_DIR) for output in view.outputs) and not codex_trusted(
        view.root, machine.codex
    ):
        found.append(
            f"Codex ignores the project's .codex/ until the project is trusted; add "
            f'[projects."{view.root}"] with trust_level = "trusted" to {machine.codex_file}'
        )
    project = _toml_text(view.codex_config)
    if "developer_instructions" in project and "developer_instructions" in machine.codex:
        found.append(
            f"{CODEX_CONFIG} sets developer_instructions, which replaces the one in "
            f"{machine.codex_file} for this project"
        )
    return tuple(found)


def _toml_text(text: str | None) -> Mapping[str, Any]:
    try:
        return tomllib.loads(text or "")
    except tomllib.TOMLDecodeError:
        return {}


def _pi_notes(view: TargetView, machine: Machine, harnesses: Sequence[str]) -> tuple[str, ...]:
    if "pi" not in harnesses or not any(
        output in PI_FILES or output.startswith(PI_PREFIXES) for output in view.outputs
    ):
        return ()
    decision = pi_decision(view.root, machine.pi_trust)
    if decision is True:
        return ()
    saved = (
        f"{machine.pi_trust_file} records this folder as not trusted"
        if decision is False
        else f"{machine.pi_trust_file} holds no decision for this folder, so Pi follows its "
        'defaultProjectTrust setting (default "ask")'
    )
    return (f"Pi loads .pi/ and .agents/skills only once the project is trusted; {saved}",)


def _shadowing(stealth: Mapping[str, Any], team: Mapping[str, Any]) -> tuple[str, ...]:
    servers = stealth.get("mcp")
    team_servers = team.get("mcp")
    same = (
        [name for name in servers if name in team_servers]
        if isinstance(servers, dict) and isinstance(team_servers, dict)
        else []
    )
    rules = stealth.get("permission")
    team_rules = team.get("permission")
    conflicting: list[str] = []
    if isinstance(rules, dict) and isinstance(team_rules, dict):
        for key, value in rules.items():
            theirs = team_rules.get(key)
            if isinstance(value, dict) and isinstance(theirs, dict):
                conflicting += [
                    f"{key}.{pattern}"
                    for pattern, verdict in value.items()
                    if pattern in theirs and theirs[pattern] != verdict
                ]
            elif key in team_rules and theirs != value:
                conflicting.append(key)
    return (
        *(
            f"{TEAM_OPENCODE} is tracked and also defines the MCP server {name}; it outranks "
            "the stealth source's opencode.json, so its definition wins"
            for name in same
        ),
        *(
            f"{TEAM_OPENCODE} is tracked and sets a different permission for {rule}; it "
            "outranks the stealth source's opencode.json, so its rule wins"
            for rule in conflicting
        ),
    )


def _skill_names(outputs: Sequence[str], folder: str) -> frozenset[str]:
    return frozenset(
        PurePosixPath(output).relative_to(folder).parts[0]
        for output in outputs
        if output.startswith(f"{folder}/")
    )


def _skill_notes(view: TargetView, machine: Machine, harnesses: Sequence[str]) -> tuple[str, ...]:
    found: list[str] = []
    if (
        "opencode" in harnesses
        and _skill_names(view.outputs, SKILL_FOLDERS["claude"])
        and machine.opencode_race
    ):
        found.append(
            "OpenCode also reads .claude/skills, so a skill rendered for both reaches it twice "
            "and it resolves them by race; set OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1"
        )
    shared = _skill_names(view.outputs, SHARED_SKILLS)
    found += [
        f"the skill {name} is in .agents/skills and in {folder} because its rendering differs "
        "between harnesses, so the harness that reads both sees it twice"
        for folder in (SKILL_FOLDERS["cursor"], SKILL_FOLDERS["opencode"], SKILL_FOLDERS["pi"])
        for name in sorted(shared & _skill_names(view.outputs, folder))
    ]
    return tuple(found)


def _cursor_notes(view: TargetView, harnesses: Sequence[str]) -> tuple[str, ...]:
    if "cursor" not in harnesses:
        return ()
    imports = "while its third-party imports are on (the default)"
    rule = any(output.startswith(".cursor/rules/") for output in view.outputs)
    instructions = (
        f"Cursor also reads CLAUDE.local.md {imports}, so it loads the stealth source's "
        "instructions twice"
    )
    read = _skill_names(view.outputs, SKILL_FOLDERS["cursor"]) | _skill_names(
        view.outputs, SHARED_SKILLS
    )
    return (
        *(instructions,) * (rule and "CLAUDE.local.md" in view.outputs),
        *(
            f"Cursor also reads .claude/skills {imports}, so the skill {name} reaches it twice"
            for name in sorted(read & _skill_names(view.outputs, SKILL_FOLDERS["claude"]))
        ),
    )


def _branch_notes(view: TargetView) -> tuple[str, ...]:
    return tuple(
        f"{unit} is tracked on the local branch {', '.join(branches)}; checking it out replaces "
        "the stealth source's copy and drops hand edits to it, so make the edits in the stealth source"
        for unit, branches in view.branches.items()
        if branches
    )


def target_notes(view: TargetView, machine: Machine, harnesses: Sequence[str]) -> tuple[str, ...]:
    lines = (
        *_codex_notes(view, machine),
        *_pi_notes(view, machine, harnesses),
        *_shadowing(view.stealth_opencode, view.team_opencode),
        *_skill_notes(view, machine, harnesses),
        *_cursor_notes(view, harnesses),
        *_branch_notes(view),
        *(PI_MCP_NOTE,) * view.pi_mcp_edited,
        *view.render_notes,
        *(
            f"saved by the harness, not in the stealth source: {rule}; add it to the stealth source's "
            "permissions.toml or delete it"
            for rule in view.carried
        ),
    )
    return tuple(f"{shown(view.root)}: {line}" for line in lines)


def global_ignore_hint(
    state_answer: str, personal: Sequence[str], covered: frozenset[str], file: Path
) -> tuple[str, ...]:
    missing = [unit for unit in personal if unit not in covered]
    if state_answer != "unasked" or not missing:
        return ()
    verb = "is" if len(missing) == 1 else "are"
    return (
        (
            f"{', '.join(missing)} {verb} ignored only per repository; sync in a terminal offers a "
            f"global ignore in {file} that covers every repository"
        ),
    )


def _renderer_notes(manifest: StealthManifest) -> tuple[str, ...]:
    rules = global_rules(manifest.manifest)
    found: list[notices.Notice] = []
    for target in manifest.manifest.permissions:
        if target.harness is None or target.name != target.harness or not target.select_all:
            continue
        if rules.default:
            found.extend(
                notices.unreached_catch_all(target.harness, target.renderer, rules.default)
            )
        found.extend(notices.unsupported_shell_globs(target.harness, target.renderer, rules))
        found.extend(notices.inexact_shell_entries(target.harness, target.renderer, rules))
    return tuple(dict.fromkeys(notice.render() for notice in found))


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _planned_text(plan: TargetPlan, relative: str) -> str | None:
    planned = plan.writes.get(plan.root / relative)
    return planned if isinstance(planned, str) else _read(plan.root / relative)


def _owned_opencode(plan: TargetPlan) -> Mapping[str, Any]:
    document = plan.record.documents.get(plan.root / OPENCODE_CONFIG)
    if document is None:
        return {}
    owned = {path[0] for path in document.owned_keys}
    parsed = _json(_planned_text(plan, OPENCODE_CONFIG))
    return {key: value for key, value in parsed.items() if key in owned}


def _view(plan: TargetPlan) -> tuple[TargetView, tuple[str, ...]]:
    root = plan.root
    paths = (*plan.record.files, *plan.record.documents, *plan.writes)
    outputs = tuple(dict.fromkeys(_rel(root, path) for path in paths))
    errors: list[str] = []
    team: Mapping[str, Any] = {}
    branches: Mapping[str, tuple[str, ...]] = {}
    try:
        if plan.fate is None:
            team = (
                _json(_read(root / TEAM_OPENCODE)) if tracked_units(root, [TEAM_OPENCODE]) else {}
            )
            branches = branches_tracking(root, path_units(root, paths))
    except (LoadoutError, OSError) as error:
        errors.append(f"git: {error}")
    view = TargetView(
        root=root,
        outputs=outputs,
        codex_config=_planned_text(plan, CODEX_CONFIG) if CODEX_CONFIG in outputs else None,
        stealth_opencode=_owned_opencode(plan),
        team_opencode=team,
        pi_mcp_edited=any(
            item.path == root / PI_MCP and item.kind == "hand-edited" for item in plan.drift
        ),
        branches=branches,
        carried=plan.carried,
        render_notes=plan.notes,
    )
    return view, tuple(errors)


def _dump(value: Any) -> str:
    return escape_surrogates(json.dumps(value, indent=2, ensure_ascii=False)) + "\n"


def _leaf_drift(file: Path, path: JsonPath, kind: DriftKind, actual: str, expected: str) -> Drift:
    return Drift(
        path=Path(f"{file}#{display_path(path)}"), actual=actual, expected=expected, kind=kind
    )


def _holds(entry: TargetRecord | None) -> bool:
    return entry is not None and bool(entry.leaves or entry.pending)


_LEAF_KINDS: dict[str, DriftKind] = {
    "create": "missing",
    "update": "source-changed",
    "retire": "orphaned",
    "owned-key": "owned-key",
    "owned-key-removed": "owned-key-removed",
}


def leaf_drift(
    record: StealthRecord, plans: Sequence[TargetPlan]
) -> tuple[tuple[Drift, ...], tuple[tuple[Path | None, str], ...]]:
    involved = [
        plan
        for plan in plans
        if not plan.whole and (plan.wanted_leaves or _holds(record.targets.get(plan.root)))
    ]
    if not involved:
        return (), ()
    try:
        work = leaf_work(record, involved, claude_json_path(), harness_homes().home)
    except LoadoutError as error:
        return (), ((None, str(error)),)
    found: list[Drift] = []
    errors: list[tuple[Path | None, str]] = []
    for file, item in work.items():
        if not item.wanted and not item.retiring:
            continue
        try:
            document = read_claude_document(file)
        except LoadoutError as error:
            errors.append((None, str(error)))
            continue
        verdicts = judge_leaves(
            document, item.wanted, item.owned, item.retiring, pending=item.pending
        )
        for verdict in verdicts:
            if verdict.kind == "user-defined":
                errors.append((Path(verdict.path[1]), f"refused: {leaf_refusal(verdict)}"))
            elif (kind := _LEAF_KINDS.get(verdict.kind)) is not None:
                present, on_disk = lookup(document, verdict.path)
                wanted_value = item.wanted.get(verdict.path)
                found.append(
                    _leaf_drift(
                        file,
                        verdict.path,
                        kind,
                        _dump(on_disk) if present else "",
                        "" if wanted_value is None else _dump(wanted_value),
                    )
                )
    return tuple(found), tuple(errors)


def _skill_folders(root: Path, manifest: StealthManifest) -> dict[Path, Readers]:
    harnesses = sorted({item.harness for item in manifest.manifest.skills})
    selected = frozenset(harnesses)
    folders: dict[Path, set[tuple[str, str]]] = {}
    for harness in harnesses:
        folder = root / str(STEALTH_PRESET[harness]["skills"].output)
        for reader in stealth_seams.readers(folder, "skills", harness, selected):
            folders.setdefault(folder, set()).add(("skills", reader))
    if any(
        target.harness == "codex" and target.name == "codex"
        for target in manifest.manifest.permissions
    ):
        folders.setdefault(root / ".codex" / "rules", set()).add(("rules", "codex"))
    return {folder: frozenset(found) for folder, found in folders.items()}


def _guarded(plan: TargetPlan) -> bool:
    return any(refusal.kind == "guard" for refusal in plan.refusals)


def _held_back_notes(
    plans: Sequence[TargetPlan], manifest: StealthManifest | None
) -> tuple[str, ...]:
    guarded = [plan for plan in plans if plan.whole and _guarded(plan)]
    if not guarded:
        return ()
    try:
        offers = manifest is not None and bool(global_servers(manifest.manifest))
        label = shown(claude_json_path())
    except (LoadoutError, OSError):
        return ()
    return tuple(
        f"{shown(plan.root)}: {label} server entries were not compared while a hand-edited "
        "output holds this target back; resolve it, then run check again"
        for plan in guarded
        if offers or _holds(plan.record)
    )


def _all_tracked(root: Path, unit: Path, listed: frozenset[str]) -> bool:
    if unit.is_symlink() or not unit.is_dir():
        return True
    return all(
        _rel(root, child) in listed
        for child in unit.rglob("*")
        if child.is_symlink() or child.is_file()
    )


def unmanaged_drift(
    plan: TargetPlan, manifest: StealthManifest, records: Sequence[StealthRecord]
) -> tuple[tuple[Drift, ...], tuple[tuple[Path | None, str], ...]]:
    root = plan.root
    owned = frozenset(plan.record.files) | frozenset(plan.writes)
    folders = {
        folder: readers
        for folder, readers in _skill_folders(root, manifest).items()
        if any(path.is_relative_to(folder) for path in owned)
    }
    if (plan.whole and not _guarded(plan)) or not folders:
        return (), ()
    try:
        accounted = (
            owned
            | every_recorded_path(folders)
            | recorded_paths_within(records, folders)
            | frozenset(harness_owned(resolve_destination))
        )
        found = unmanaged_in(folders, accounted)
        units = [_rel(root, path) for path in found]
        listed = tracked_files(root, units)
        exempt = frozenset(
            root / unit
            for unit in units_in(listed, units)
            if _all_tracked(root, root / unit, listed)
        )
    except UnreadableRecords as error:
        return (), tuple((None, record_problem_message(problem)) for problem in error.problems)
    except (LoadoutError, OSError) as error:
        return (), ((root, str(error)),)
    return (
        tuple(
            Drift(path=path, actual=_listing(path), expected="", kind="unmanaged")
            for path in found
            if path not in exempt
        ),
        (),
    )


def _listing(path: Path) -> str:
    if path.is_dir() and not path.is_symlink():
        return "".join(
            f"{child.relative_to(path)}\n" for child in sorted(path.rglob("*")) if child.is_file()
        )
    return f"{path.name}\n"


def _by_hand(refusal: Refusal) -> bool:
    return refusal.kind == "unparsable" and refusal.code < 3  # noqa: PLR2004


def _refusal_error(refusal: Refusal) -> str | None:
    if refusal.kind in ("guard", "abort") or _by_hand(refusal):
        return None
    return refusal.message if refusal.kind == "error" else f"refused: {refusal.message}"


def _hint(plans: Sequence[TargetPlan], others: Sequence[StealthRecord]) -> tuple[str, ...]:
    mine = {
        unit
        for unit in PERSONAL_UNITS
        if any(
            plan.root / unit in (*plan.record.files, *plan.record.documents)
            for plan in plans
            if not plan.whole
        )
    }
    theirs = set(personal_units(others)) if mine else set()
    personal = tuple(unit for unit in PERSONAL_UNITS if unit in mine | theirs)
    state = read_global_state()
    root = next((plan.root for plan in plans if plan.fate is None), None)
    if state.answer != "unasked" or not personal or root is None:
        return ()
    file = state.file or excludes_file(root)
    with scratch_repo() as scratch:
        covered = user_covered(scratch, file, personal)
    return global_ignore_hint(state.answer, personal, covered, file)


def _stealth_notes(
    manifest: StealthManifest | None,
    plans: Sequence[TargetPlan],
    others: Sequence[StealthRecord],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    try:
        hint = _hint(plans, others) if plans else ()
    except (LoadoutError, OSError) as error:
        return (), (f"global gitignore: {error}",)
    renderer = _renderer_notes(manifest) if manifest is not None else ()
    return (*renderer, *hint), ()


def check_stealth(stealth: RegisteredStealth, scope: Path | None) -> StealthCheck:
    run = StealthRun(stealth=stealth, scope=scope, force=False, interactive=False)
    prepared = prepare(run)
    record, expansion, plans = prepared.record, prepared.expansion, prepared.plans
    prepared_errors, shared = prepared.errors, prepared.shared
    try:
        manifest: StealthManifest | None = load_stealth_manifest(stealth.folder)
    except (LoadoutError, OSError):
        manifest = None
    machine = read_machine(os.environ)
    harnesses = manifest.harnesses if manifest is not None else ()

    leaves, leaf_errors = leaf_drift(record, plans)
    views = [_view(plan) for plan in plans]
    records = every_stealth_record() if plans else ()
    others = [found for found in records if not same_path(found.folder, stealth.folder)]
    unmanaged = (
        [unmanaged_drift(plan, manifest, records) for plan in plans] if manifest is not None else []
    )
    stealth_notes, note_errors = _stealth_notes(manifest, plans, others)
    skipped = expansion.skipped

    drift = (
        *shared,
        *(item for plan in plans for item in plan.drift),
        *leaves,
        *(item for found, _ in unmanaged for item in found),
    )
    errors: tuple[tuple[Path | None, str], ...] = tuple(
        dict.fromkeys(
            [
                *((None, error) for error in prepared_errors),
                *leaf_errors,
                *((None, error) for error in note_errors),
                *(
                    (plan.root, message)
                    for plan in plans
                    for refusal in plan.refusals
                    if (message := _refusal_error(refusal)) is not None
                ),
                *(
                    (plan.root, error)
                    for plan, (_, found) in zip(plans, views, strict=True)
                    for error in found
                ),
                *(error for _, found in unmanaged for error in found),
                *((skip.folder, f"skipped: {skip.reason}") for skip in skipped if skip.failure),
            ]
        )
    )
    notes = (
        *expansion_notes(expansion),
        *stealth_notes,
        *_held_back_notes(plans, manifest),
        *(
            f"{shown(plan.root)}: {refusal.message}"
            for plan in plans
            for refusal in plan.refusals
            if _by_hand(refusal)
        ),
        *(f"{shown(skip.folder)}: skipped: {skip.reason}" for skip in skipped if not skip.failure),
        *(note for view, _ in views for note in target_notes(view, machine, harnesses)),
    )
    code = max(
        0,
        1 if drift else 0,
        3 if errors else 0,
        *(plan.code for plan in plans),
    )
    return StealthCheck(
        name=stealth.name,
        drift=drift,
        notes=notes,
        errors=errors,
        code=code,
        json_labels=frozenset(item.path for item in leaves),
    )


def report_check(check: StealthCheck) -> None:
    lead = f"stealth {check.name}"
    for target, message in check.errors:
        where = "" if target is None else f"{shown(target)}: "
        print(f"{lead}: ERROR: {where}{message}", file=sys.stderr)
    for item in check.drift:
        label = shown(item.path)
        print(f"{lead}: DRIFT ({item.kind}): {label}", file=sys.stderr)
        format_name = "json" if item.path in check.json_labels else None
        report_diff(
            diff_lines(label, item.actual, item.expected, context=3, format_name=format_name)
        )
    for note in check.notes:
        print(f"note: {lead}: {note}")
    if not check.drift and not check.errors:
        print(f"{lead}: up to date")
