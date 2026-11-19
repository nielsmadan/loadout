from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from .claude_json import escape_surrogates
from .emit import ProjectRender, RenderContext, render_project_detailed
from .errors import LoadoutError
from .exclude import anchored, block_after, block_before, write_personal_block
from .files import document_format
from .gitignore import gitignore_to_remove
from .guarded_write import ChangedDuringSync, write_if_unchanged
from .output import Merged, Output
from .personal import NO_PERSONAL, PersonalReport, PersonalTier, personal_tier
from .project import generated_units, gitignore_entries
from .stealth_git import identity, reincluded, tracked_units
from .surgery import apply
from .written import ABSENT, PersonalRecord, WrittenEntry, merged_entry, recorded_keys


@dataclass(frozen=True)
class PersonalView:
    outputs: dict[Path, Output]
    team: dict[Path, Output]
    written: Mapping[Path, WrittenEntry]
    report: PersonalReport
    personal: tuple[Path, ...] = ()
    held: frozenset[Path] = frozenset()
    released: frozenset[Path] = frozenset()
    lines: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    active: bool = False


def sync_tier(root: Path, retiring: Collection[str] = ()) -> PersonalTier:
    resolved = str(root.resolve())
    if "\n" in resolved or "\r" in resolved:
        return NO_PERSONAL
    return personal_tier(root, retiring=retiring)


def _team(outputs: Mapping[Path, Output], project: ProjectRender) -> dict[Path, Output]:
    own = {path: output for path, output in outputs.items() if path not in project.outputs}
    return {**own, **project.team}


def _unit(root: Path, path: Path) -> tuple[str, bool]:
    unit = generated_units(root, (path,))[0]
    return unit, unit != path.relative_to(root).as_posix()


def _by_unit(root: Path, paths: Iterable[Path], skip: frozenset[str]) -> dict[str, list[Path]]:
    found: dict[str, list[Path]] = {}
    for path in paths:
        if not path.is_relative_to(root):
            continue
        unit, _ = _unit(root, path)
        if unit not in skip:
            found.setdefault(unit, []).append(path)
    return found


def _line(root: Path, unit: str, paths: Sequence[Path]) -> str:
    return anchored(unit, folder=any(_unit(root, path)[1] for path in paths))


def team_claimed(root: Path, paths: Iterable[Path]) -> tuple[frozenset[Path], tuple[str, ...]]:
    from .stealth_plan import tracked_note  # noqa: PLC0415 - it imports commands

    units = _by_unit(root, paths, frozenset())
    if not units:
        return frozenset(), ()
    folders = frozenset(
        unit for unit, found in units.items() if any(_unit(root, path)[1] for path in found)
    )
    tracked = tracked_units(root, units)
    loose = reincluded(root, [unit for unit in units if unit not in tracked], folders)
    claimed = [unit for unit in units if unit in tracked or unit in loose]
    notes = tuple(
        tracked_note(unit)
        if unit in tracked
        else f"{loose[unit]} re-includes {unit}; it is left in place and no longer recorded"
        for unit in claimed
    )
    return frozenset(path for unit in claimed for path in units[unit]), notes


def team_only(
    outputs: Mapping[Path, Output],
    project: ProjectRender | None,
    written: Mapping[Path, WrittenEntry],
    record: PersonalRecord,
    reason: str | None = None,
) -> PersonalView:
    report = project.report if project is not None else PersonalReport()
    team = dict(outputs) if project is None else _team(outputs, project)
    if reason is None:
        return PersonalView(dict(outputs), team, written, report)
    kept = frozenset({*record.personal, *record.pending})
    return PersonalView(
        team,
        team,
        {path: entry for path, entry in written.items() if path not in kept},
        report,
        refusals=(f"refused: {reason}; the personal tier was not synced",),
    )


def personal_view(
    root: Path,
    outputs: Mapping[Path, Output],
    project: ProjectRender | None,
    written: Mapping[Path, WrittenEntry],
    record: PersonalRecord,
) -> PersonalView:
    from .stealth_plan import tracked_note, tracked_refusal  # noqa: PLC0415 - it imports commands

    if project is None:
        return team_only(outputs, project, written, record)
    team = _team(outputs, project)
    personal = [path for path in outputs if path not in team]
    shared = [
        path
        for path in outputs
        if path in project.outputs and path in project.team and outputs[path] != project.team[path]
    ]
    recorded = frozenset({*record.personal, *record.pending})
    held = recorded - set(outputs) if project.report.held else frozenset()
    if not (personal or shared or recorded or record.contributors):
        return team_only(outputs, project, written, record)
    team_units = frozenset(generated_units(root, team))
    writing = _by_unit(root, personal, team_units)
    orphans = _by_unit(root, recorded - set(outputs) - held, team_units)
    shared_units = {path: _unit(root, path)[0] for path in shared if path.is_relative_to(root)}
    names = tuple(dict.fromkeys((*writing, *orphans, *shared_units.values())))
    folders = frozenset(
        unit for unit, paths in writing.items() if any(_unit(root, path)[1] for path in paths)
    )
    tracked = tracked_units(root, names) if names else frozenset()
    loose = reincluded(root, tuple(writing), folders) if writing else {}
    kept = dict(outputs)
    refusals: list[str] = []
    notes: list[str] = []
    released: set[Path] = set()
    dropped = frozenset(unit for unit in writing if unit in tracked or unit in loose)
    for unit in dropped:
        paths = writing[unit]
        for path in paths:
            kept.pop(path, None)
        before = [
            path for path in recorded if path.is_relative_to(root) and _unit(root, path)[0] == unit
        ]
        if before:
            released.update((*before, *paths))
            notes.append(
                tracked_note(unit)
                if unit in tracked
                else f"{loose[unit]} re-includes {unit}; it is left in place and no longer recorded"
            )
        elif unit in tracked:
            refusals.append(f"refused: {tracked_refusal(root, unit).message}")
        else:
            refusals.append(
                f"refused: {unit} would show in git status: {loose[unit]} re-includes it"
            )
    for unit, paths in orphans.items():
        if unit in tracked:
            released.update(paths)
            notes.append(tracked_note(unit))
    for path, unit in shared_units.items():
        if unit in tracked:
            kept[path] = project.team[path]
            refusals.append(f"refused: {tracked_refusal(root, unit).message}")
    lines = [
        _line(root, unit, paths)
        for unit, paths in {**_by_unit(root, held, team_units), **writing}.items()
        if unit not in dropped
    ]
    return PersonalView(
        kept,
        team,
        {path: entry for path, entry in written.items() if path not in held | released},
        project.report,
        personal=tuple(path for path in personal if path in kept),
        held=held,
        released=frozenset(released),
        lines=tuple(dict.fromkeys(lines)),
        refusals=tuple(dict.fromkeys(refusals)),
        notes=tuple(dict.fromkeys(notes)),
        active=True,
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


def hide_after(common: Path, root: Path, lines: Sequence[str]) -> None:
    write_personal_block(
        common,
        root,
        lambda current: block_after(lines, [line for line in current if _on_disk(root, line)]),
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
            applied = apply(existing, merged.owned, merged.document, format_name=merged.format)
            if expected is None and empty_document(applied, merged.format):
                continue
            if expected is not None and escape_surrogates(applied).encode("utf-8") == expected:
                continue
            write_if_unchanged(path, applied, expected, merged.format)
        except ChangedDuringSync:
            refused.append(path)
            refusals.append(f"refused: {_shown(root, path)} changed while syncing; run sync again")
            continue
        except (LoadoutError, UnicodeError, OSError) as error:
            refused.append(path)
            refusals.append(f"refused: {_shown(root, path)}: {error}")
            continue
        written.append(path)
        if expected is None:
            created.append(path)
    return DocumentWrites(tuple(written), tuple(created), tuple(refused), tuple(refusals))


def _shown(root: Path, path: Path) -> str:
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


def path_owners(
    root: Path,
    context: RenderContext,
    paths: Sequence[Path],
    report: PersonalReport,
    recorded: Mapping[Path, str],
) -> dict[Path, str]:
    contributors = report.contributors
    found: dict[Path, str] = {}
    if len(contributors) > 1:
        wanted = frozenset(paths)
        for name in contributors:
            alone = PersonalTier(tuple(s for s in context.personal.sources if s.name == name))
            render = render_project_detailed(root, replace(context, personal=alone))
            found.update(
                {
                    path: name
                    for path in render.outputs
                    if path in wanted and path not in render.team and path not in found
                }
            )
    fallback = (*contributors, *report.held)
    owners = {
        path: found.get(path) or recorded.get(path) or (fallback[0] if fallback else None)
        for path in paths
    }
    return {path: owner for path, owner in owners.items() if owner is not None}
