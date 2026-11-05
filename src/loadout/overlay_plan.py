from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from . import overlay_seams
from .claude_json import escape_surrogates, server_path
from .diffs import diff_lines, withheld
from .errors import LoadoutError
from .exclude import TEMP_PATTERN, anchored
from .files import Drift, DriftKind, check_all
from .json_paths import JsonPath, display_path
from .output import Copied, digests
from .overlay_documents import (
    DocumentKind,
    DocumentRecord,
    DocumentVerdict,
    OwnedJson,
    apply_owned,
    judge_document,
    judge_strip,
    strip_owned,
)
from .overlay_record import OverlayRecord, TargetRecord, united_document
from .overlay_render import OVERLAY_PRESET, OverlayRender
from .overlay_targets import Expansion, RecordedFate, Target
from .retirement import orphan_drift
from .sync import Unaccepted, unaccepted_against_record
from .written import WrittenEntry, copied_entry, text_entry

RefusalKind = Literal[
    "tracked",
    "claimed",
    "guard",
    "abort",
    "user-defined",
    "unparsable",
    "unsafe",
    "unignored",
    "error",
]
RecordedAction = Literal["plan", "retire", "keep"]
TargetFate = Literal["gone", "left"]

DIFF_CONTEXT = 3

_SKILL_DIRS = tuple(
    PurePosixPath(str(OVERLAY_PRESET[harness]["skills"].output)) for harness in OVERLAY_PRESET
)
_GUARDED: frozenset[DocumentKind] = frozenset({"owned-key", "owned-key-removed"})
_SOURCE_CHANGES: frozenset[DocumentKind] = frozenset({"create", "update", "retire"})


@dataclass(frozen=True)
class GitFacts:
    tracked: frozenset[str]
    covered: frozenset[str]
    branches: Mapping[str, tuple[str, ...]]
    reincluded: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiskFacts:
    documents: Mapping[Path, dict[str, Any] | None]
    document_bytes: Mapping[Path, bytes | None]
    claimed: Mapping[Path, str]
    claimed_leaves: Mapping[JsonPath, str]


@dataclass(frozen=True)
class Refusal:
    kind: RefusalKind
    code: int
    message: str
    path: Path | None = None
    diff: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetPlan:
    root: Path
    whole: bool
    writes: Mapping[Path, str | Copied]
    deletes: tuple[Path, ...]
    block_before: tuple[str, ...]
    block_after: tuple[str, ...]
    ahead: TargetRecord
    record: TargetRecord
    wanted_leaves: Mapping[JsonPath, Any]
    retiring_leaves: Mapping[Path, tuple[JsonPath, ...]]
    refusals: tuple[Refusal, ...]
    drift: tuple[Drift, ...]
    carried: tuple[str, ...]
    notes: tuple[str, ...]
    code: int
    fate: TargetFate | None = None


def _unmatched_action(
    root: Path, expansion: Expansion, fates: Mapping[Path, RecordedFate]
) -> RecordedAction:
    if any(root.is_relative_to(parent) for parent in expansion.unlisted_under):
        return "keep"
    return "retire" if fates.get(root) in ("gone", "present") else "keep"


def _retire_guard(
    record: OverlayRecord, expansion: Expansion, retiring: int, *, home: Path, force: bool
) -> Refusal | None:
    if not retiring:
        return None
    if record.home != home:
        return Refusal(
            "error",
            3,
            f"the targets were matched under HOME {record.home}, and HOME is now {home}; "
            f"nothing is retired — run with HOME={record.home} to retire {retiring} targets",
        )
    if not expansion.matched and not force:
        return Refusal(
            "error",
            3,
            "the patterns matched no folder; nothing is retired — fix `targets`, or run "
            f"`loadout sync --overlay {record.name} --force` to retire {retiring} targets",
        )
    return None


def classify_recorded(  # noqa: PLR0913
    record: OverlayRecord,
    expansion: Expansion,
    fates: Mapping[Path, RecordedFate],
    *,
    scope: Path | None,
    home: Path,
    force: bool,
) -> tuple[dict[Path, RecordedAction], tuple[Refusal, ...]]:
    planned = frozenset(target.root for target in expansion.targets)
    skipped = frozenset(skip.folder for skip in expansion.skipped)
    matched: dict[Path, RecordedAction] = {
        root: "plan" if root in planned else "keep"
        for root in record.targets
        if root in planned or root in skipped or (scope is None and root in expansion.matched)
    }
    unmatched = {
        root: _unmatched_action(root, expansion, fates)
        for root in record.targets
        if scope is None and root not in expansion.matched
    }
    retiring = sum(action == "retire" for action in unmatched.values())
    refusal = _retire_guard(record, expansion, retiring, home=home, force=force)
    settled: dict[Path, RecordedAction] = {
        root: "keep" if refusal is not None else action for root, action in unmatched.items()
    }
    actions = {
        root: matched.get(root) or settled[root]
        for root in record.targets
        if root in matched or root in settled
    }
    return actions, (refusal,) if refusal is not None else ()


def _unit(root: Path, path: Path) -> tuple[str, bool]:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    for directory in _SKILL_DIRS:
        if relative.is_relative_to(directory) and relative != directory:
            return f"{directory}/{relative.relative_to(directory).parts[0]}", True
    return str(relative), False


def _orphans(
    rendered: Mapping[Path, str | Copied | OwnedJson], entry: TargetRecord
) -> tuple[Path, ...]:
    return (
        *(path for path in entry.files if path not in rendered),
        *(path for path in entry.documents if path not in rendered),
    )


def unit_of(root: Path, path: Path) -> str:
    return _unit(root, path)[0]


def folder_units(root: Path, paths: Iterable[Path]) -> frozenset[str]:
    return frozenset(unit for unit, folder in (_unit(root, path) for path in paths) if folder)


def tracked_refusal(root: Path, unit: str) -> Refusal:
    return Refusal(
        "tracked", 3, f"the team tracks {unit}; the overlay does not write it", root / unit
    )


def tracked_note(unit: str) -> str:
    return f"the team tracks {unit}; it is left in place and no longer recorded"


def plan_units(
    root: Path, render: OverlayRender | None, recorded: TargetRecord | None
) -> tuple[str, ...]:
    rendered = render.files if render is not None else {}
    entry = recorded if recorded is not None else _blank_entry(root / ".git", 0)
    paths = (*rendered, *_orphans(rendered, entry))
    return tuple(dict.fromkeys(_unit(root, path)[0] for path in paths))


def path_units(root: Path, paths: Iterable[Path]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_unit(root, path)[0] for path in paths))


def _owner(path: Path, claimed: Mapping[Path, str]) -> str | None:
    return next((claimed[folder] for folder in (path, *path.parents) if folder in claimed), None)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _blank_entry(common_dir: Path, device: int) -> TargetRecord:
    return TargetRecord(
        common_dir=common_dir,
        device=device,
        block=None,
        files={},
        documents={},
        leaves={},
        pending={},
    )


def _serialized(document: Mapping[str, Any] | None) -> str:
    return (
        "" if document is None else escape_surrogates(overlay_seams.serialize_json(dict(document)))
    )


@dataclass(frozen=True)
class _Document:
    write: str | None
    delete: bool
    record: DocumentRecord | None
    guard: Refusal | None
    guard_kinds: frozenset[DocumentKind]
    refusals: tuple[Refusal, ...]
    drift: Drift | None
    carried: tuple[str, ...]


_UNTOUCHED = _Document(None, False, None, None, frozenset(), (), None, ())


def _without(owned: OwnedJson, paths: frozenset[JsonPath]) -> OwnedJson:
    return OwnedJson(
        keys=tuple((path, value) for path, value in owned.keys if path not in paths),
        rules=tuple((path, entries) for path, entries in owned.rules if path not in paths),
    )


def _guard_message(rel: str, verdicts: Iterable[DocumentVerdict]) -> str:
    edited = [display_path(v.path) for v in verdicts if v.kind == "owned-key"]
    removed = [display_path(v.path) for v in verdicts if v.kind == "owned-key-removed"]
    parts = [
        *([f"{', '.join(edited)} edited outside loadout"] if edited else []),
        *([f"{', '.join(removed)} removed outside loadout"] if removed else []),
    ]
    return f"{rel}: {'; '.join(parts)}"


def _guard_refusal(
    path: Path, rel: str, verdicts: tuple[DocumentVerdict, ...], diff: list[str]
) -> Refusal | None:
    guarded = tuple(v for v in verdicts if v.kind in _GUARDED)
    if not guarded:
        return None
    return Refusal("guard", 1, _guard_message(rel, guarded), path, tuple(diff))


def _document_drift_kind(
    existing: Mapping[str, Any] | None, kinds: frozenset[DocumentKind]
) -> DriftKind | None:
    if existing is None:
        return "missing" if kinds else None
    if "owned-key" in kinds:
        return "owned-key"
    if "owned-key-removed" in kinds:
        return "owned-key-removed"
    return "source-changed" if kinds & _SOURCE_CHANGES else None


def _drift_of(path: Path, kind: DriftKind | None, before: str, after: str) -> Drift | None:
    return None if kind is None else Drift(path=path, actual=before, expected=after, kind=kind)


def _user_defined(rel: str, path: Path, found: JsonPath) -> Refusal:
    return Refusal(
        "user-defined",
        3,
        f"{rel}: {display_path(found)} already holds a value the overlay did not write; "
        "remove it or move it into the overlay source",
        path,
    )


def _unappliable(rel: str, path: Path, error: LoadoutError) -> Refusal:
    return Refusal(
        "unparsable",
        1,
        f"{rel}: {error}; loadout does not overwrite it — fix the value by hand",
        path,
    )


def _plan_document(
    root: Path,
    path: Path,
    owned: OwnedJson,
    existing: dict[str, Any] | None,
    recorded: DocumentRecord | None,
) -> _Document:
    rel = _rel(root, path)
    defined = frozenset(
        v.path for v in judge_document(existing, owned, recorded) if v.kind == "user-defined"
    )
    kept = _without(owned, defined)
    verdicts = judge_document(existing, kept, recorded)
    kinds = frozenset(v.kind for v in verdicts)
    user_defined = tuple(_user_defined(rel, path, found) for found in sorted(defined))
    try:
        applied = apply_owned(existing, kept, recorded)
    except LoadoutError as error:
        shown = _serialized(existing)
        return replace(
            _UNTOUCHED,
            record=recorded,
            refusals=(*user_defined, _unappliable(rel, path, error)),
            drift=Drift(path=path, actual=shown, expected=shown, kind="unparsable"),
        )
    before, after = _serialized(existing), _serialized(applied.document)
    owns = bool(applied.record.owned_keys or applied.record.owned_rules)
    return _Document(
        write=after if applied.changed else None,
        delete=False,
        record=applied.record if owns or recorded is not None else None,
        guard=_guard_refusal(path, rel, verdicts, diff_lines(rel, before, after, DIFF_CONTEXT)),
        guard_kinds=kinds & _GUARDED,
        refusals=user_defined,
        drift=_drift_of(path, _document_drift_kind(existing, kinds), before, after),
        carried=tuple(f"{rel}: {entry}" for entry in applied.carried),
    )


def _strip_document(
    root: Path, path: Path, existing: dict[str, Any] | None, recorded: DocumentRecord
) -> _Document:
    if existing is None:
        return _UNTOUCHED
    rel = _rel(root, path)
    verdicts = judge_strip(existing, recorded)
    kinds = frozenset(v.kind for v in verdicts)
    stripped = strip_owned(existing, recorded)
    before, after = _serialized(existing), _serialized(stripped)
    changed = stripped is None or stripped != existing
    guard = _guard_refusal(path, rel, verdicts, diff_lines(rel, before, after, DIFF_CONTEXT))
    kind: DriftKind | None = "owned-key" if guard is not None else "orphaned" if changed else None
    return _Document(
        write=after if stripped is not None and changed else None,
        delete=stripped is None,
        record=None,
        guard=guard,
        guard_kinds=kinds & _GUARDED,
        refusals=(),
        drift=_drift_of(path, kind, before, after),
        carried=(),
    )


def _file_guard(root: Path, found: Unaccepted) -> Refusal:
    rel = _rel(root, found.path)
    message = (
        f"{rel} exists and loadout did not write it"
        if found.kind == "unknown"
        else f"{rel} was edited outside loadout"
        + ("; it is no longer rendered" if found.orphaned else "")
    )
    diff = diff_lines(rel, found.actual, found.expected, DIFF_CONTEXT)
    return Refusal("guard", 1, message, found.path, tuple(diff))


def force_command(name: str, *, retire_all: bool) -> str:
    action = f"overlay remove {name}" if retire_all else f"sync --overlay {name}"
    return f"`loadout {action} --force`"


def _removal_abort(shown: set[str], unshown: set[str], command: str) -> tuple[str, ...]:
    lost = f"Removal stopped — the '-' lines would be lost. Run {command} to discard them."
    withheld_lost = (
        "Removal stopped — a file whose diff is not shown was changed outside loadout, and "
        f"the change would be lost. Read the file, then run {command} to discard it."
    )
    return (*([lost] if shown else []), *([withheld_lost] if unshown else []))


def _abort(
    guards: tuple[tuple[Refusal, frozenset[str]], ...], name: str, *, retire_all: bool
) -> tuple[Refusal, ...]:
    shown = {k for refusal, kinds in guards if not withheld(list(refusal.diff)) for k in kinds}
    unshown = {k for refusal, kinds in guards if withheld(list(refusal.diff)) for k in kinds}
    command = force_command(name, retire_all=retire_all)
    if retire_all:
        return tuple(
            Refusal("abort", 1, sentence) for sentence in _removal_abort(shown, unshown, command)
        )
    return tuple(
        Refusal("abort", 1, sentence.replace("`loadout sync --force`", command))
        for sentence in (
            *overlay_seams.abort_sentences(shown),
            *overlay_seams.abort_sentences(unshown, shown=False),
        )
    )


def _file_drift(
    drift: Iterable[Drift], verdicts: Mapping[Path, DriftKind], orphans: Mapping[Path, WrittenEntry]
) -> tuple[Drift, ...]:
    def kind(item: Drift) -> DriftKind:
        if item.kind == "unparsable":
            return item.kind
        if not item.path.exists():
            return "missing"
        if item.path in orphans:
            return verdicts.get(item.path, "orphaned")
        return verdicts.get(item.path, "source-changed")

    return tuple(replace(item, kind=kind(item)) for item in drift)


def shared_drift(
    wanted: Mapping[Path, str],
    recorded: Mapping[Path, WrittenEntry],
    retiring: Mapping[Path, WrittenEntry],
) -> tuple[Drift, ...]:
    findings = unaccepted_against_record(wanted, recorded, retiring)
    return _file_drift(
        (*check_all(wanted), *orphan_drift(retiring)),
        {found.path: found.kind for found in findings},
        retiring,
    )


def _block(units: Iterable[tuple[str, bool]], covered: frozenset[str]) -> tuple[str, ...]:
    patterns = tuple(
        anchored(unit, folder=folder)
        for unit, folder in dict.fromkeys(units)
        if unit not in covered
    )
    return (*patterns, TEMP_PATTERN) if patterns else ()


def _new_entry(path: Path, output: str | Copied, sources: Callable[[Path], str]) -> WrittenEntry:
    if isinstance(output, Copied):
        return copied_entry(output.source, sources)
    return text_entry(path, output)


@dataclass(frozen=True)
class _Split:
    whole: dict[Path, str | Copied]
    owned: dict[Path, OwnedJson]
    orphan_files: dict[Path, WrittenEntry]
    orphan_docs: dict[Path, DocumentRecord]
    held: frozenset[Path]
    refusals: tuple[Refusal, ...]
    notes: tuple[str, ...]


def _split(
    root: Path,
    rendered: Mapping[Path, str | Copied | OwnedJson],
    entry: TargetRecord,
    git: GitFacts,
    claims: Mapping[Path, str],
) -> _Split:
    def unit(path: Path) -> str:
        return _unit(root, path)[0]

    orphans = _orphans(rendered, entry)
    tracked_units = frozenset(
        unit(path)
        for path in (*rendered, *orphans)
        if unit(path) in git.tracked or _rel(root, path) in git.tracked
    )
    tracked = frozenset(path for path in (*rendered, *orphans) if unit(path) in tracked_units)
    loose = frozenset(
        path for path in rendered if path not in tracked and unit(path) in git.reincluded
    )
    claimed = {
        path: owner
        for path in (*rendered, *orphans)
        if path not in tracked and path not in loose and (owner := _owner(path, claims)) is not None
    }
    dropped = tracked | loose | claimed.keys()
    tracked_out = dict.fromkeys(unit(path) for path in rendered if path in tracked)
    tracked_orphans = dict.fromkeys(
        unit(path) for path in orphans if path in tracked and unit(path) not in tracked_out
    )
    loose_out = dict.fromkeys(unit(path) for path in rendered if path in loose)
    claimed_out = dict.fromkeys((unit(p), owner) for p, owner in claimed.items() if p in rendered)
    claimed_orphans = dict.fromkeys(
        (unit(p), owner) for p, owner in claimed.items() if p not in rendered
    )
    return _Split(
        whole={
            path: output
            for path, output in rendered.items()
            if path not in dropped and not isinstance(output, OwnedJson)
        },
        owned={
            path: output
            for path, output in rendered.items()
            if path not in dropped and isinstance(output, OwnedJson)
        },
        orphan_files={
            path: found
            for path, found in entry.files.items()
            if path not in rendered and path not in dropped
        },
        orphan_docs={
            path: found
            for path, found in entry.documents.items()
            if path not in rendered and path not in dropped
        },
        held=loose | frozenset(path for path in tracked if unit(path) in tracked_out),
        refusals=(
            *(tracked_refusal(root, u) for u in tracked_out),
            *(
                Refusal(
                    "unignored",
                    3,
                    f"{u} would show in git status: {git.reincluded[u]} re-includes it",
                    root / u,
                )
                for u in loose_out
            ),
            *(
                Refusal(
                    "claimed",
                    3,
                    f"{u} is written by {owner}; the overlay does not write it",
                    root / u,
                )
                for u, owner in claimed_out
            ),
        ),
        notes=(
            *(tracked_note(u) for u in tracked_orphans),
            *(
                f"{u} is now written by {owner}; the overlay no longer records it"
                for u, owner in claimed_orphans
            ),
        ),
    )


def _leaves(
    root: Path, servers: Mapping[str, Any], entry: TargetRecord, claims: Mapping[JsonPath, str]
) -> tuple[dict[JsonPath, Any], dict[Path, tuple[JsonPath, ...]], tuple[Refusal, ...]]:
    paths = {server_path(root, server): value for server, value in servers.items()}
    wanted = {path: value for path, value in paths.items() if path not in claims}
    owned = {
        file: dict.fromkeys([*entry.leaves.get(file, {}), *entry.pending.get(file, {})])
        for file in dict.fromkeys([*entry.leaves, *entry.pending])
    }
    retiring = {
        file: kept
        for file, held in owned.items()
        if (kept := tuple(path for path in held if path not in wanted and path not in claims))
    }
    refusals = tuple(
        Refusal(
            "claimed",
            3,
            f"MCP server {path[-1]} is written by {claims[path]}; the overlay does not write it",
        )
        for path in paths
        if path in claims
    )
    return wanted, retiring, refusals


def _wholesale(root: Path, entry: TargetRecord, refusals: tuple[Refusal, ...]) -> TargetPlan:
    return TargetPlan(
        root=root,
        whole=True,
        writes={},
        deletes=(),
        block_before=(),
        block_after=(),
        ahead=entry,
        record=entry,
        wanted_leaves={},
        retiring_leaves={},
        refusals=refusals,
        drift=(),
        carried=(),
        notes=(),
        code=max((refusal.code for refusal in refusals), default=0),
    )


def _records(
    target: Target,
    entry: TargetRecord,
    held: frozenset[Path],
    new_files: Mapping[Path, WrittenEntry],
    new_documents: Mapping[Path, DocumentRecord],
) -> tuple[TargetRecord, TargetRecord]:
    ahead = replace(
        entry,
        common_dir=target.common_dir,
        device=target.device,
        files={**new_files, **entry.files},
        documents={
            **entry.documents,
            **{
                path: united_document(entry.documents.get(path), found)
                for path, found in new_documents.items()
            },
        },
    )
    record = replace(
        entry,
        common_dir=target.common_dir,
        device=target.device,
        files={
            **{path: found for path, found in entry.files.items() if path in held},
            **new_files,
        },
        documents={
            **{path: found for path, found in entry.documents.items() if path in held},
            **new_documents,
        },
    )
    return ahead, record


def plan_target(  # noqa: PLR0913
    target: Target,
    render: OverlayRender | None,
    recorded: TargetRecord | None,
    git: GitFacts,
    disk: DiskFacts,
    *,
    name: str,
    force: bool,
    retire_all: bool = False,
) -> TargetPlan:
    root = target.root
    entry = recorded if recorded is not None else _blank_entry(target.common_dir, target.device)
    rendered = render.files if render is not None else {}
    split = _split(root, rendered, entry, git, disk.claimed)
    sources = digests()
    findings = unaccepted_against_record(split.whole, entry.files, split.orphan_files, sources)
    documents = {
        path: _plan_document(root, path, owned, disk.documents[path], entry.documents.get(path))
        for path, owned in split.owned.items()
    }
    stripped = {
        path: _strip_document(root, path, disk.documents[path], found)
        for path, found in split.orphan_docs.items()
    }
    judged = (*documents.values(), *stripped.values())
    wanted, retiring, leaf_refusals = _leaves(
        root, render.claude_servers if render is not None else {}, entry, disk.claimed_leaves
    )
    refusals = (
        *split.refusals,
        *(found for document in judged for found in document.refusals),
        *leaf_refusals,
    )
    notes = (*(render.notes if render is not None else ()), *split.notes)
    drift = (
        *_file_drift(
            (*check_all(split.whole), *orphan_drift(split.orphan_files)),
            {found.path: found.kind for found in findings},
            split.orphan_files,
        ),
        *(document.drift for document in judged if document.drift is not None),
    )
    carried = tuple(item for document in documents.values() for item in document.carried)
    guarded: tuple[tuple[Refusal, frozenset[str]], ...] = (
        *((_file_guard(root, found), frozenset({found.kind})) for found in findings),
        *(
            (document.guard, frozenset(document.guard_kinds))
            for document in judged
            if document.guard is not None
        ),
    )
    guards = tuple(refusal for refusal, _ in guarded)
    if guards and not force:
        whole = _wholesale(
            root, entry, (*refusals, *guards, *_abort(guarded, name, retire_all=retire_all))
        )
        return replace(whole, drift=drift, carried=carried, notes=notes)

    deletes = (
        *sorted(split.orphan_files),
        *(path for path, document in stripped.items() if document.delete),
    )
    present = (
        *(
            path
            for path in rendered
            if path in split.whole or (path in documents and documents[path].record is not None)
        ),
        *split.orphan_files,
        *(path for path in split.orphan_docs if disk.documents[path] is not None),
    )
    block_before = _block((_unit(root, path) for path in present), git.covered)
    released = frozenset(deletes) | (
        frozenset(split.orphan_docs) if render is None else frozenset()
    )
    block_after = _block(
        (_unit(root, path) for path in present if path not in released), git.covered
    )
    ahead, record = _records(
        target,
        entry,
        split.held,
        {path: _new_entry(path, output, sources) for path, output in split.whole.items()},
        {
            path: document.record
            for path, document in documents.items()
            if document.record is not None
        },
    )
    return TargetPlan(
        root=root,
        whole=False,
        writes={
            **split.whole,
            **{
                path: document.write
                for path, document in {**documents, **stripped}.items()
                if document.write is not None
            },
        },
        deletes=deletes,
        block_before=block_before,
        block_after=block_after,
        ahead=replace(ahead, block=entry.block or (name if block_before else None)),
        record=replace(record, block=name if block_after else None),
        wanted_leaves=wanted,
        retiring_leaves=retiring,
        refusals=refusals,
        drift=drift,
        carried=carried,
        notes=notes,
        code=max((refusal.code for refusal in refusals), default=0),
    )


def kept(root: Path, recorded: TargetRecord | None, refusal: Refusal) -> TargetPlan:
    entry = recorded if recorded is not None else _blank_entry(root / ".git", 0)
    return _wholesale(root, entry, (refusal,))
