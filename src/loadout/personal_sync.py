from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .claude_json import escape_surrogates
from .containment import refuse_escaping
from .emit import ProjectRender, RenderContext
from .errors import LoadoutError
from .exclude import block_after, block_before, write_personal_block
from .files import document_format
from .gitignore import gitignore_to_remove, remove_lines
from .guarded_write import ChangedDuringSync, write_if_unchanged
from .output import Merged, Output
from .personal_plan import PersonalView, owner, team_claimed, team_only, unparsable_refusal
from .project import gitignore_entries
from .retirement import retire
from .state import SyncState
from .stealth_git import identity
from .surgery import apply
from .sync import (
    Unaccepted,
    deploy,
    newly_managed,
    refuse_unparsable_orphans,
    retire_orphans,
    unaccepted_against_record,
)
from .written import (
    ABSENT,
    PersonalRecord,
    WrittenEntry,
    merged_entry,
    owned_values,
    read_personal,
    read_written,
    record_written,
    recorded_keys,
)


def common_dir(root: Path) -> Path:
    found = identity(root.resolve())
    if found is None:
        raise LoadoutError(f"{root} is not in a git repository, so nothing can hide its files")
    return found.common_dir


def hide_before(common: Path, root: Path, lines: Sequence[str]) -> None:
    write_personal_block(common, root, lambda current: block_before(current, lines))


_UNESCAPE = re.compile(r"\\(.)")


def _on_disk(root: Path, line: str) -> bool:
    return (root / _UNESCAPE.sub(r"\1", line.strip("/"))).exists()


def lines_to_drop(
    root: Path,
    recorded: Iterable[str],
    team: Mapping[Path, Output],
    outputs: Mapping[Path, Output],
) -> tuple[str, ...]:
    written = frozenset(gitignore_entries(root, outputs))
    return tuple(
        line
        for line in gitignore_to_remove(recorded, gitignore_entries(root, team))
        if line in written or not _on_disk(root, line)
    )


def hide_after(common: Path, root: Path, lines: Sequence[str], team: Mapping[Path, Output]) -> None:
    claimed = frozenset(gitignore_entries(root, team))
    write_personal_block(
        common,
        root,
        lambda current: block_after(
            lines,
            [line for line in current if line not in claimed and _on_disk(root, line)],
        ),
    )


def empty_document(text: str, format_name: str) -> bool:
    try:
        parsed = json.loads(text) if format_name == "json" else tomllib.loads(text)
    except (ValueError, tomllib.TOMLDecodeError):
        return False
    return parsed == {}


@dataclass(frozen=True)
class DocumentWrites:
    written: tuple[Path, ...] = ()
    created: tuple[Path, ...] = ()
    refused: tuple[Path, ...] = ()
    refusals: tuple[str, ...] = ()


def write_documents(
    root: Path, documents: Mapping[Path, Merged], read: Mapping[Path, bytes | None]
) -> DocumentWrites:
    written: list[Path] = []
    created: list[Path] = []
    refused: list[Path] = []
    refusals: list[str] = []
    for path, merged in documents.items():
        expected = read.get(path)
        try:
            existing = "" if expected is None else expected.decode("utf-8")
            if owned_values(existing, merged.owned, merged.format) is None:
                refused.append(path)
                refusals.append(unparsable_refusal(root, path, orphaned=False))
                continue
            applied = apply(existing, merged.owned, merged.document, format_name=merged.format)
            if expected is None and empty_document(applied, merged.format):
                continue
            if expected is not None and escape_surrogates(applied).encode("utf-8") == expected:
                continue
            write_if_unchanged(path, applied, expected, merged.format)
        except ChangedDuringSync:
            refused.append(path)
            refusals.append(f"refused: {_rel(root, path)} changed while syncing; run sync again")
            continue
        except (LoadoutError, UnicodeError, OSError) as error:
            refused.append(path)
            refusals.append(f"refused: {_rel(root, path)}: {error}")
            continue
        written.append(path)
        if expected is None:
            created.append(path)
    return DocumentWrites(tuple(written), tuple(created), tuple(refused), tuple(refusals))


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def document_entries(
    documents: Mapping[Path, Merged],
    previous: Mapping[Path, WrittenEntry],
    *,
    held: bool,
) -> dict[Path, WrittenEntry]:
    entries = {path: merged_entry(merged) for path, merged in documents.items()}
    if not held:
        return entries
    return {path: _with_held_keys(entry, previous.get(path)) for path, entry in entries.items()}


def _with_held_keys(entry: WrittenEntry, previous: WrittenEntry | None) -> WrittenEntry:
    kept = recorded_keys(previous) - set(entry.owned)
    if previous is None or not kept:
        return entry
    hashes = dict(previous.owned_sha256)
    return replace(
        entry,
        owned=tuple(sorted({*entry.owned, *kept})),
        owned_sha256=tuple(
            sorted(
                {
                    **dict(entry.owned_sha256),
                    **{key: hashes[key] for key in kept if hashes.get(key, ABSENT) != ABSENT},
                }.items()
            )
        ),
    )


def empty_created(created: Iterable[Path]) -> tuple[Path, ...]:
    def emptied(path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        return empty_document(text, document_format(path))

    return tuple(path for path in created if path.is_file() and emptied(path))


@dataclass(frozen=True)
class Removal:
    retiring: frozenset[str] = frozenset()
    keep_edits: bool = False


NO_REMOVAL = Removal()


@dataclass(frozen=True)
class PersonalRun:
    root: Path
    context: RenderContext
    view: PersonalView
    state: SyncState
    record: PersonalRecord
    common: Path | None


@dataclass(frozen=True)
class Deployed:
    written: list[Path]
    managed: list[tuple[Path, str, str]]
    ignored: int
    created: tuple[Path, ...]
    refusals: tuple[str, ...]


@dataclass(frozen=True)
class Written:
    retired: list[Path]
    unignored: int


@dataclass(frozen=True)
class RemovalEdits:
    released: frozenset[Path] = frozenset()
    left: frozenset[Path] = frozenset()


def recorded_edits(record: PersonalRecord, modified: Sequence[Unaccepted]) -> frozenset[Path]:
    recorded = {*record.personal, *record.pending}
    return frozenset(item.path for item in modified if item.path in recorded)


def removal_edits(
    record: PersonalRecord, modified: Sequence[Unaccepted], removal: Removal
) -> RemovalEdits:
    if not (removal.retiring and removal.keep_edits):
        return RemovalEdits()
    edited = recorded_edits(record, modified)
    retiring = min(removal.retiring)
    released = frozenset(
        item.path
        for item in modified
        if item.path in edited
        and item.orphaned
        and record.personal.get(item.path, retiring) in removal.retiring
    )
    return RemovalEdits(released, edited - released)


def released(view: PersonalView, kept: frozenset[Path]) -> PersonalView:
    return replace(
        view,
        written={path: entry for path, entry in view.written.items() if path not in kept},
        released=view.released | kept,
    )


def record_ahead(
    root: Path,
    view: PersonalView,
    rendered: tuple[Mapping[Path, Output], ProjectRender | None],
    state: SyncState,
    record: PersonalRecord,
) -> tuple[PersonalView, Path | None]:
    contributors = (*record.contributors, *view.report.contributors)
    try:
        if (
            record_written(
                root,
                {},
                pending=frozenset({*record.pending, *view.personal}),
                contributors=dict.fromkeys(contributors),
            )
            is None
        ):
            raise LoadoutError("the written record cannot be saved")
        common = common_dir(root)
        hide_before(common, root, view.lines)
    except LoadoutError as error:
        return team_only(*rendered, state.written, record, str(error)), None
    return view, common


def write_outputs(
    run: PersonalRun,
    documents: Mapping[Path, Merged],
    as_read: Mapping[Path, bytes | None],
    sources: Callable[[Path], str],
    read: Mapping[Path, bytes | None],
) -> Deployed:
    root, view, state = run.root, run.view, run.state
    # Read before writing: once deployed, the value a key held is gone.
    managed = newly_managed(state, view.outputs)
    rest = {path: output for path, output in view.outputs.items() if path not in documents}
    written, ignored = deploy(state, rest, sources, read, ignored=view.team)
    wrote = write_documents(root, documents, as_read)
    if documents:
        record_written(
            root,
            document_entries(
                {
                    path: merged
                    for path, merged in documents.items()
                    if path.is_file() and path not in wrote.refused
                },
                state.written,
                held=bool(view.report.held),
            ),
            dropped=[
                path for path in documents if not path.is_file() and path not in wrote.refused
            ],
            created=([*{*run.record.created, *wrote.created}] if run.common is not None else None),
        )
    return Deployed([*written, *wrote.written], managed, ignored, wrote.created, wrote.refusals)


def finish_writes(run: PersonalRun, deployed: Deployed) -> Written:
    root, view, state = run.root, run.view, run.state
    personal = frozenset({*run.record.personal, *run.record.pending, *view.personal})
    retired, unignored = retire_orphans(state, view.outputs, view.team, personal)
    created = frozenset({*run.record.created, *deployed.created}) - view.released
    emptied = empty_created(created) if run.common is not None else ()
    for path in emptied:
        path.unlink()
    if state.project:
        unignored += drop_team_lines(root, view.team, view.outputs)
    if run.common is not None:
        _record_personal(run, run.common, created, emptied)
    return Written(list(dict.fromkeys((*retired, *emptied))), unignored)


def drop_team_lines(root: Path, team: Mapping[Path, Output], outputs: Mapping[Path, Output]) -> int:
    recorded = read_personal(root).gitignored
    unwanted = lines_to_drop(root, recorded, team, outputs)
    if not unwanted:
        return 0
    removed = remove_lines(root, unwanted)
    record_written(root, {}, gitignored=[line for line in recorded if line not in unwanted])
    return removed


def _record_personal(
    run: PersonalRun, common: Path, created: frozenset[Path], emptied: Sequence[Path]
) -> None:
    root, view = run.root, run.view
    gone = frozenset(emptied)
    owners = {
        path: found
        for path in (*view.held, *view.personal)
        if (found := owner(path, view.renderers, run.record.personal, view.report)) is not None
    }
    record_written(
        root,
        {},
        dropped=[*view.released, *gone],
        personal={
            path: source
            for path, source in owners.items()
            if path not in gone and path not in view.released
        },
        pending=(),
        common_dir=common,
        created=[
            path
            for path in created
            if path.is_file() and path not in gone and (path in view.personal or path in view.held)
        ],
        contributors=dict.fromkeys((*view.report.contributors, *view.report.held)),
    )
    hide_after(common, root, view.lines, view.team)


@dataclass(frozen=True)
class Retired:
    retired: tuple[Path, ...] = ()
    unignored: int = 0
    notes: tuple[str, ...] = ()
    kept: tuple[Path, ...] = ()


@dataclass(frozen=True)
class _Stranded:
    paths: frozenset[Path]
    left: frozenset[Path]
    notes: tuple[str, ...]
    orphans: dict[Path, WrittenEntry]
    kept: frozenset[Path]


def _stranded(
    root: Path, record: PersonalRecord, written: Mapping[Path, WrittenEntry]
) -> _Stranded:
    paths = frozenset({*record.personal, *record.pending})
    left, notes = team_claimed(root, paths)
    orphans = {path: entry for path, entry in written.items() if path in paths - left}
    refuse_escaping(root, orphans)
    refuse_unparsable_orphans(SyncState(root, False, orphans), {})
    kept = frozenset(item.path for item in unaccepted_against_record({}, written, orphans))
    return _Stranded(paths, left, notes, orphans, kept)


def stranded_preview(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    record = read_personal(root)
    if not (record.personal or record.pending):
        return (), ()
    found = _stranded(root, record, read_written(root))
    retired = sorted(path for path in found.orphans if path not in found.kept and path.is_file())
    return tuple(retired), tuple(sorted(found.kept))


def retire_tier(root: Path) -> Retired:
    record = read_personal(root)
    if not (record.personal or record.pending or record.contributors):
        return Retired()
    written = read_written(root)
    found = _stranded(root, record, written)
    paths, left, notes, orphans, kept = (
        found.paths,
        found.left,
        found.notes,
        found.orphans,
        found.kept,
    )
    retired = retire({path: entry for path, entry in orphans.items() if path not in kept}, written)
    emptied = empty_created(record.created - kept - left)
    for path in emptied:
        path.unlink()
    if record.common_dir is not None:
        write_personal_block(record.common_dir, root, lambda _: ())
    unignored = drop_team_lines(root, {}, {})
    record_written(root, {}, dropped=paths, personal={}, pending=(), created=(), contributors=())
    return Retired(
        tuple(dict.fromkeys((*retired, *emptied))), unignored, notes, tuple(sorted(kept))
    )
