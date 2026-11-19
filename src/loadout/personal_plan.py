from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .emit import ProjectRender, RenderContext, render_project_detailed
from .errors import LoadoutError
from .exclude import anchored
from .files import check_all, document_format, unparsable, unparsable_as
from .output import Merged, Output
from .personal import NO_PERSONAL, PersonalReport, PersonalTier, personal_tier
from .project import PROJECT_PRESET, generated_units, project_slices
from .stealth_git import reincluded, tracked_message, tracked_note, tracked_units
from .stealth_preset import STEALTH_PRESET
from .written import PersonalRecord, WrittenEntry


@dataclass(frozen=True)
class PersonalView:
    outputs: dict[Path, Output]
    team: dict[Path, Output]
    written: Mapping[Path, WrittenEntry]
    report: PersonalReport
    personal: tuple[Path, ...] = ()
    held: frozenset[Path] = frozenset()
    released: frozenset[Path] = frozenset()
    unparsable: frozenset[Path] = frozenset()
    lines: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    active: bool = False
    renderers: Mapping[Path, tuple[str, ...]] = field(default_factory=dict)


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


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def reinclude_note(source: str, unit: str) -> str:
    return f"{source} re-includes {unit}; it is left in place and no longer recorded"


def team_claimed(root: Path, paths: Iterable[Path]) -> tuple[frozenset[Path], tuple[str, ...]]:
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
        tracked_note(unit) if unit in tracked else reinclude_note(loose[unit], unit)
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


def planned_view(  # noqa: PLR0913
    root: Path,
    outputs: Mapping[Path, Output],
    project: ProjectRender | None,
    written: Mapping[Path, WrittenEntry],
    record: PersonalRecord,
    *,
    context: RenderContext | None = None,
    retiring: Collection[str] = (),
) -> PersonalView:
    try:
        return personal_view(
            root, outputs, project, written, record, context=context, retiring=retiring
        )
    except LoadoutError as error:
        return team_only(outputs, project, written, record, str(error))


def _unparsable_documents(
    outputs: Mapping[Path, Output],
    personal: Iterable[Path],
    written: Mapping[Path, WrittenEntry],
    orphans: Iterable[Path],
) -> dict[Path, bool]:
    rendered = {
        path: False
        for path in personal
        if isinstance(output := outputs[path], Merged) and unparsable(path, output)
    }
    gone = {
        path: True
        for path in orphans
        if (entry := written.get(path)) is not None
        and entry.kind == "merged"
        and unparsable_as(path, document_format(path), entry.owned)
    }
    return {**rendered, **gone}


def unparsable_refusal(
    root: Path,
    path: Path,
    *,
    orphaned: bool,
    source: str | None = None,
    retiring: Collection[str] = (),
) -> str:
    named = f"stealth {source}: " if source is not None else ""
    verb = "remove its keys from" if orphaned else "write its keys into"
    rerun = f"loadout stealth remove {source}" if source in retiring else "loadout sync"
    return (
        f"refused: {named}{_rel(root, path)} is not a {document_format(path).upper()} document "
        f"loadout can {verb}; fix or delete it, then run `{rerun}`"
    )


def personal_view(  # noqa: PLR0913
    root: Path,
    outputs: Mapping[Path, Output],
    project: ProjectRender | None,
    written: Mapping[Path, WrittenEntry],
    record: PersonalRecord,
    *,
    context: RenderContext | None = None,
    retiring: Collection[str] = (),
) -> PersonalView:
    if project is None:
        return team_only(outputs, project, written, record)
    team = _team(outputs, project)
    rendered = [path for path in outputs if path not in team]
    shared = [
        path
        for path in outputs
        if path in project.outputs and path in project.team and outputs[path] != project.team[path]
    ]
    recorded = frozenset({*record.personal, *record.pending})
    held = recorded - set(outputs) if project.report.held else frozenset()
    if not (rendered or shared or recorded or record.contributors):
        return team_only(outputs, project, written, record)
    broken = _unparsable_documents(outputs, rendered, written, recorded - set(outputs) - held)
    personal = [path for path in rendered if path not in broken]
    held |= frozenset(path for path in broken if path in recorded)
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
    kept = {path: output for path, output in outputs.items() if path not in broken}
    refusals: list[str] = []
    notes: list[str] = []
    released: set[Path] = set()
    dropped = frozenset(unit for unit in writing if unit in tracked or unit in loose)
    refused_units = {
        **{unit: writing[unit] for unit in dropped},
        **{
            unit: [path for path, found in shared_units.items() if found == unit]
            for unit in dict.fromkeys(shared_units.values())
            if unit in tracked
        },
    }
    renderers = (
        rendered_by(root, context, project.report, {*rendered, *shared})
        if context is not None
        else {}
    )
    sources = sole_sources(refused_units, renderers)

    def refused(unit: str, message: str) -> str:
        source = f"stealth {sources[unit]}: " if unit in sources else ""
        return f"refused: {source}{message}"

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
                tracked_note(unit) if unit in tracked else reinclude_note(loose[unit], unit)
            )
        elif unit in tracked:
            refusals.append(refused(unit, tracked_message(unit)))
        else:
            refusals.append(
                refused(unit, f"{unit} would show in git status: {loose[unit]} re-includes it")
            )
    for unit, paths in orphans.items():
        if unit in tracked:
            released.update(paths)
            notes.append(tracked_note(unit))
    for path, unit in shared_units.items():
        if unit in tracked:
            kept[path] = project.team[path]
            refusals.append(refused(unit, tracked_message(unit)))
    refusals.extend(
        unparsable_refusal(
            root,
            path,
            orphaned=orphaned,
            source=owner(path, renderers, record.personal, project.report),
            retiring=retiring,
        )
        for path, orphaned in broken.items()
    )
    frozen = _held_documents(root, kept, team) if project.report.held else ()
    for path in frozen:
        kept.pop(path)
        refusals.append(
            f"refused: stealth {', '.join(project.report.held)}: {_rel(root, path)} is left as "
            "it is while the source is held, so none of its rules are dropped"
        )
    held |= frozenset(path for path in frozen if path not in team)
    lines = [
        _line(root, unit, paths)
        for unit, paths in {**_by_unit(root, held, team_units), **writing}.items()
        if unit not in dropped
    ]
    return PersonalView(
        kept,
        team,
        {path: entry for path, entry in written.items() if path not in {*held, *released, *frozen}},
        project.report,
        personal=tuple(path for path in personal if path in kept),
        held=held,
        released=frozenset(released),
        unparsable=frozenset(broken),
        lines=tuple(dict.fromkeys(lines)),
        refusals=tuple(dict.fromkeys(refusals)),
        notes=tuple(dict.fromkeys(notes)),
        active=True,
        renderers=renderers,
    )


def left_as_is(view: PersonalView, paths: frozenset[Path]) -> PersonalView:
    return replace(
        view,
        outputs={path: output for path, output in view.outputs.items() if path not in paths},
        written={path: entry for path, entry in view.written.items() if path not in paths},
        personal=tuple(path for path in view.personal if path not in paths),
        held=view.held | paths,
    )


def _held_documents(
    root: Path, outputs: Mapping[Path, Output], team: Mapping[Path, Output]
) -> tuple[Path, ...]:
    shared = {
        root / spec.output
        for harness, name, spec in project_slices(PROJECT_PRESET)
        if spec.output is not None
        and (name in {"permissions", "mcp-permissions"} or (name == "mcp" and harness != "claude"))
    }
    composite = {
        root / str(STEALTH_PRESET[harness]["instructions"].output)
        for harness in ("claude", "codex", "droid", "pi")
    }
    return tuple(
        path
        for path, output in outputs.items()
        if (path in shared if path in team else path in composite)
        and path.is_file()
        and check_all({path: output})
    )


def rendered_by(
    root: Path,
    context: RenderContext,
    report: PersonalReport,
    paths: Collection[Path],
) -> dict[Path, tuple[str, ...]]:
    contributors = report.contributors
    if len(contributors) <= 1:
        return dict.fromkeys(paths, contributors) if contributors else {}
    found: dict[Path, list[str]] = {}
    for name in contributors:
        alone = PersonalTier(tuple(s for s in context.personal.sources if s.name == name))
        render = render_project_detailed(root, replace(context, personal=alone))
        for path in render.outputs.keys() & paths:
            if render.outputs[path] != render.team.get(path):
                found.setdefault(path, []).append(name)
    return {path: tuple(names) for path, names in found.items()}


def owner(
    path: Path,
    renderers: Mapping[Path, Sequence[str]],
    recorded: Mapping[Path, str],
    report: PersonalReport,
) -> str | None:
    names = renderers.get(path, ())
    return names[0] if names else recorded.get(path) or (report.held[0] if report.held else None)


def sole_sources(
    units: Mapping[str, Sequence[Path]], renderers: Mapping[Path, Sequence[str]]
) -> dict[str, str]:
    owners = {
        unit: {name for path in paths for name in renderers.get(path, ())}
        for unit, paths in units.items()
    }
    return {unit: next(iter(names)) for unit, names in owners.items() if len(names) == 1}
