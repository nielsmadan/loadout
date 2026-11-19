from __future__ import annotations

import contextlib
import json
import shlex
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .atomic import atomic_install
from .bundled_skill import bundled_skill_path
from .color import BOLD_YELLOW, GREEN, print_styled
from .containment import refuse_escaping
from .diffs import diff_lines, report_diff, withheld
from .emit import (
    SKILLS_SUBDIR,
    ProjectKits,
    ProjectRender,
    RenderContext,
    kit_skills,
    refuse_unregistered_global,
    render_all_detailed,
)
from .errors import LoadoutError, UsageError
from .exclude import write_personal_block
from .files import Drift, DriftKind, pi_settings_as_read, refuse_unparsable
from .git_privacy import still_tracked
from .gitignore import remove_lines
from .guarded_write import read_bytes
from .kit_catalog import load_catalogs
from .kit_discover import AvailableKit, available_kits
from .kit_vendor import kit_diff, update_vendored_directory, vendor_catalog
from .kits import (
    contained,
    copy_tree,
    declare,
    has_local_edits,
    kit_files,
    record_hash,
    remove_vendored,
    resolve_kit,
    resolve_upstream_kit,
    tree_hash,
    unrecord_hash,
    vendored_files,
    vendored_path,
)
from .machine import Registration, fixed, registration, state_dir
from .notices import collect_notices
from .output import FrozenFile, Merged, Output, digests, output_record_paths
from .personal_sync import (
    PersonalView,
    common_dir,
    document_entries,
    empty_created,
    hide_after,
    hide_before,
    lines_to_drop,
    path_owners,
    personal_view,
    sync_tier,
    team_claimed,
    team_only,
    write_documents,
)
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    generated_units,
    load_project_config,
    project_config_path,
)
from .retirement import retire
from .skill_installation import (
    SkillSourceLocation,
    SourceSkillState,
    configured_skill_agents,
    copied_as_written,
    inspect_skill_source,
    install_skill_source,
    uninstall_skill_source,
)
from .skills import SKILL_DOCUMENT, Skill, discover_skills
from .state import SyncState, sync_state
from .stealth_lock import project_lock
from .sync import (
    Unaccepted,
    classified_drift,
    deploy,
    newly_managed,
    refuse_unparsable_orphans,
    retire_orphans,
    unaccepted_against_record,
    unaccepted_outputs,
)
from .unmanaged import unmanaged_entries
from .written import (
    PersonalRecord,
    prune_vanished,
    read_personal,
    read_written,
    record_written,
)

KitMode = Literal["linked", "vendored"]


def _print_skill_location(
    root: Path, profile: str, source_name: str | None
) -> tuple[Path, SkillSourceLocation, tuple[str, ...]]:
    bundle = bundled_skill_path()
    location = inspect_skill_source(root, profile, bundle, source_name)
    agents = configured_skill_agents(root, profile)
    print(f"source {location.source}: {location.path} ({location.state.value})")
    print(f"configured harnesses: {', '.join(agents) if agents else 'none'}")
    return bundle, location, agents


def _confirm_skill_change(action: str, path: Path, yes: bool) -> bool:
    if yes:
        return True
    try:
        response = input(f"{action} the loadout skill at {path} and sync global config? [y/N] ")
    except (EOFError, OSError) as error:
        raise UsageError(f"skill {action} requires --yes when input is not interactive") from error
    return response.strip().lower() in {"y", "yes"}


def cmd_skill_status(root: Path, profile: str, source_name: str | None = None) -> int:
    _print_skill_location(root, profile, source_name)
    return 0


def cmd_skill_install(
    root: Path,
    profile: str,
    source_name: str | None = None,
    *,
    yes: bool = False,
) -> int:
    agents = configured_skill_agents(root, profile)
    if not agents:
        print("configured harnesses: none")
        print("no configured harness receives global skills; no changes made")
        return 0
    bundle, location, _ = _print_skill_location(root, profile, source_name)
    if _refused(location):
        return 1
    if location.state is not SourceSkillState.INSTALLED and not _confirm_skill_change(
        "install", location.path, yes
    ):
        print("declined; no changes made")
        return 0
    changed = install_skill_source(location, bundle)
    print(
        f"{'installed' if changed else 'already installed'} loadout skill in "
        f"source {location.source}: {location.path}"
    )
    if copied_as_written(root, profile):
        print(
            "note: [skills.loadout] render = false can now be removed, so each harness gets "
            "the skill rendered for it"
        )
    return cmd_sync(root, profile=profile)


def cmd_skill_uninstall(
    root: Path,
    profile: str,
    source_name: str | None = None,
    *,
    yes: bool = False,
) -> int:
    _, location, _ = _print_skill_location(root, profile, source_name)
    if location.state is SourceSkillState.MISSING:
        print("loadout skill is not installed; no changes made")
        return 0
    if _refused(location, owned=True):
        return 1
    if not _confirm_skill_change("uninstall", location.path, yes):
        print("declined; no changes made")
        return 0
    removed = uninstall_skill_source(location, root=root, profile=profile)
    print(
        f"uninstalled loadout skill from source {location.source}: {location.path} "
        f"({len(removed)} generated files removed)"
    )
    return cmd_sync(root, profile=profile)


def _refused(location: SkillSourceLocation, *, owned: bool = False) -> bool:
    """Report a source copy loadout may not change, one it does not own or that was edited.
    Install takes over a copy identical to the bundle; `owned` refuses it too."""
    unowned = {SourceSkillState.CONFLICTING} | ({SourceSkillState.IDENTICAL} if owned else set())
    if location.state not in {*unowned, SourceSkillState.MODIFIED}:
        return False
    detail = (
        "exists and is not owned by loadout"
        if location.state in unowned
        else "was modified after installation"
    )
    print(f"loadout: {location.path} {detail}; no changes made", file=sys.stderr)
    return True


def _display(path: Path, root: Path) -> str:
    """A destination may live outside root (e.g. under ~); relative_to() would raise."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _abort_sentences(kinds: set[str], *, shown: bool = True) -> list[str]:
    """What the refused diffs mean, by kind: a removed key shows as `+` lines sync would
    write back, anything else as `-` lines it would destroy (review 3, batch 2.11). A diff
    withheld for a secret shows neither, so the file is named instead (ADR 0032)."""
    sentences = []
    if kinds - {"owned-key-removed"}:
        sentences.append(
            "Sync aborted — the '-' lines would be lost. Port them into the source with "
            "/loadout, or run `loadout sync --force` to discard them."
            if shown
            else WITHHELD_ABORT
        )
    if "owned-key-removed" in kinds:
        sentences.append(
            "Sync aborted — the '+' lines were removed outside loadout and would be written "
            "back. Remove them from the source with /loadout, or run `loadout sync --force` "
            "to write them back."
            if shown
            else WITHHELD_REMOVED_ABORT
        )
    return sentences


WITHHELD_ABORT = (
    "Sync aborted — a file whose diff is not shown was changed outside loadout, and the "
    "change would be lost. Read the file, then port it into the source with /loadout, or run "
    "`loadout sync --force` to discard it."
)
WITHHELD_REMOVED_ABORT = (
    "Sync aborted — keys loadout writes were removed outside loadout from a file whose diff "
    "is not shown, and would be written back. Read the file, then remove them from the source "
    "with /loadout too, or run `loadout sync --force` to write them back."
)

ABORT_DIFF = "sync-abort.diff"


def _write_full_diff(lines: list[str]) -> Path | None:
    """The untruncated diff, so an abort the terminal shortened stays reviewable; None
    when the state directory cannot hold it, which must not turn the abort into a crash."""
    path = state_dir() / ABORT_DIFF
    try:
        atomic_install(path, FrozenFile("".join(lines).encode("utf-8"), 0o600), path.parent)
    except OSError:
        return None
    return path


def cmd_sync(
    root: Path,
    profile: str,
    force: bool = False,
    *,
    from_hook: bool = False,
    retiring: frozenset[str] = frozenset(),
) -> int:
    root = root.absolute()
    # One read of the registration decides the gate, the render and the guard.
    registered = fixed(registration())
    refuse_unregistered_global(root, RenderContext(registration=registered))
    with project_lock(root):
        team, personal = _sync_locked(root, profile, force, registered, retiring)
    # A teammate's commit must not make every checkout's hook fail on a personal refusal.
    return team if from_hook else max(team, personal)


def _sync_locked(
    root: Path,
    profile: str,
    force: bool,
    registered: Callable[[], Registration],
    retiring: frozenset[str],
) -> tuple[int, int]:
    context = RenderContext(registration=registered, personal=sync_tier(root, retiring))
    prune_vanished()
    read = pi_settings_as_read()
    # Rendered once and reused for the guard, the write and the record, so what is
    # recorded is provably what was written rather than a third render of it.
    rendered, project = render_all_detailed(root, profile, context)
    # The root as sync finds it, before writing anything.
    before = sync_state(root)
    record = read_personal(root)
    view = _personal_view(root, rendered, project, before, record)
    documents = {
        path: output for path in view.personal if isinstance(output := view.outputs[path], Merged)
    }
    as_read = {path: read_bytes(path) for path in documents}
    state = replace(before, written=view.written)
    refuse_unparsable_orphans(state, view.outputs)
    # Each copied source is hashed once a sync: by the guard, the writer and the record.
    sources = digests()
    if not force:
        refuse_unparsable(view.outputs)
        modified = unaccepted_outputs(state, view.outputs, sources, context)
        if modified:
            _report_abort(root, modified)
            return 1, 0
    common: Path | None = None
    if view.active:
        view, common = _record_ahead(root, view, (rendered, project), before, record)
    done = _write(
        _Run(root, context, view, replace(before, written=view.written), record, common),
        {path: output for path, output in documents.items() if path in view.personal},
        as_read,
        sources,
        read,
    )
    # Only the abort it described made the full diff worth keeping.
    with contextlib.suppress(OSError):
        (state_dir() / ABORT_DIFF).unlink(missing_ok=True)
    for path in done.retired:
        print(f"retired {_display(path, root)}")
    print(_sync_summary(view.outputs, done.written, done.retired))
    if done.ignored:
        print(f"added {done.ignored} entries to .gitignore")
    if done.unignored:
        print(f"removed {done.unignored} entries from .gitignore")
    _report_notices(root, profile, context)
    # Reported, never stopped on: sync overwrites nothing there, so stopping would
    # only block the outputs it does own.
    # The state as sync left it: the record from before names what was just retired
    # (review 5, batch 2.6).
    for path in unmanaged_entries(sync_state(root), view.outputs, profile):
        advice = UNMANAGED_LINK_ADVICE if path.is_symlink() else UNMANAGED_ADVICE
        print(f"WARNING: {_display(path, root)} is {advice}", file=sys.stderr)
    for unit in _tracked_outputs(root, view.team) if before.project else ():
        print(f"WARNING: {still_tracked_note(unit)}", file=sys.stderr)
    refusals = (*view.report.refusals, *view.refusals, *done.refusals)
    for note in (*view.report.notes, *view.notes):
        print(f"note: {note}", file=sys.stderr)
    for refusal in refusals:
        print(refusal, file=sys.stderr)
    return 0, 3 if refusals or view.report.held else 0


@dataclass(frozen=True)
class _Run:
    root: Path
    context: RenderContext
    view: PersonalView
    state: SyncState
    record: PersonalRecord
    common: Path | None


@dataclass(frozen=True)
class _Done:
    written: list[Path]
    retired: list[Path]
    ignored: int
    unignored: int
    refusals: tuple[str, ...]


def _write(
    run: _Run,
    documents: Mapping[Path, Merged],
    as_read: Mapping[Path, bytes | None],
    sources: Callable[[Path], str],
    read: Mapping[Path, bytes | None],
) -> _Done:
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
    written = [*written, *wrote.written]
    for path in written:
        print(f"wrote {_display(path, root)}")
    for path, key, was in managed:
        print(f"now managed: {key} in {_display(path, root)} (was {was})")
    personal = frozenset({*run.record.personal, *run.record.pending, *view.personal})
    retired, unignored = retire_orphans(state, view.outputs, view.team, personal)
    created = frozenset({*run.record.created, *wrote.created})
    emptied = empty_created(created) if run.common is not None else ()
    for path in emptied:
        path.unlink()
    if state.project:
        unignored += _drop_team_lines(root, view.team, view.outputs)
    if run.common is not None:
        _record_personal(run, run.common, created, emptied)
    return _Done(
        written, list(dict.fromkeys((*retired, *emptied))), ignored, unignored, wrote.refusals
    )


def _personal_view(
    root: Path,
    outputs: Mapping[Path, Output],
    project: ProjectRender | None,
    state: SyncState,
    record: PersonalRecord,
) -> PersonalView:
    try:
        return personal_view(root, outputs, project, state.written, record)
    except LoadoutError as error:
        return team_only(outputs, project, state.written, record, str(error))


def _record_ahead(
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


def _drop_team_lines(
    root: Path, team: Mapping[Path, Output], outputs: Mapping[Path, Output]
) -> int:
    recorded = read_personal(root).gitignored
    unwanted = lines_to_drop(root, recorded, team, outputs)
    if not unwanted:
        return 0
    removed = remove_lines(root, unwanted)
    record_written(root, {}, gitignored=[line for line in recorded if line not in unwanted])
    return removed


def _record_personal(
    run: _Run, common: Path, created: frozenset[Path], emptied: Sequence[Path]
) -> None:
    root, view = run.root, run.view
    gone = frozenset(emptied)
    owners = path_owners(root, run.context, view.personal, view.report, run.record.personal)
    fallback = view.report.held[0] if view.report.held else None
    held = {
        path: owner
        for path in view.held
        if (owner := run.record.personal.get(path) or fallback) is not None
    }
    record_written(
        root,
        {},
        dropped=[*view.released, *gone],
        personal={
            path: source
            for path, source in {**held, **owners}.items()
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
    hide_after(common, root, view.lines)


def retire_personal(root: Path) -> int:
    root = root.absolute()
    prune_vanished()
    with project_lock(root):
        return _retire_personal_locked(root)


def _retire_personal_locked(root: Path) -> int:
    record = read_personal(root)
    if not (record.personal or record.pending or record.contributors):
        return 0
    written = read_written(root)
    paths = frozenset({*record.personal, *record.pending})
    left, notes = team_claimed(root, paths)
    orphans = {path: entry for path, entry in written.items() if path in paths - left}
    refuse_escaping(root, orphans)
    refuse_unparsable_orphans(SyncState(root, False, orphans), {})
    kept = frozenset(item.path for item in unaccepted_against_record({}, written, orphans))
    retired = retire({path: entry for path, entry in orphans.items() if path not in kept}, written)
    emptied = empty_created(record.created - kept - left)
    for path in emptied:
        path.unlink()
    if record.common_dir is not None:
        write_personal_block(record.common_dir, root, lambda _: ())
    unignored = _drop_team_lines(root, {}, {})
    record_written(root, {}, dropped=paths, personal={}, pending=(), created=(), contributors=())
    for path in dict.fromkeys((*retired, *emptied)):
        print(f"retired {_display(path, root)}")
    if unignored:
        print(f"removed {unignored} entries from .gitignore")
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    for path in sorted(kept):
        print(
            f"WARNING: kept {_display(path, root)}: it was modified since loadout wrote it, "
            "and loadout no longer manages it",
            file=sys.stderr,
        )
    return 0


def _report_abort(root: Path, modified: Sequence[Unaccepted]) -> None:
    full: list[str] = []
    truncated = False
    shown: set[str] = set()
    unshown: set[str] = set()
    for item in modified:
        rel = _display(item.path, root)
        state = " and is no longer rendered" if item.orphaned else ""
        print(
            f"WARNING: {rel} was modified outside loadout{state} ({item.kind})",
            file=sys.stderr,
        )
        # Tight context: on a 16k settings.json the one runtime-added entry
        # should be readable without scrolling past the whole document.
        lines = diff_lines(rel, item.actual, item.expected, context=1)
        full.extend(lines)
        truncated = report_diff(lines) or truncated
        (unshown if withheld(lines) else shown).add(item.kind)
    for sentence in [*_abort_sentences(shown), *_abort_sentences(unshown, shown=False)]:
        print(f"\n{sentence}", file=sys.stderr)
    # A blocking prompt the reader cannot see all of is one they learn to
    # skip, and skipping this one means reaching for --force, which is the
    # thing it exists to prevent. Whatever the terminal truncates, the file
    # holds in full — the decision stays reviewable.
    if truncated and (kept := _write_full_diff(full)) is not None:
        print(f"\nThe complete diff is at {kept}", file=sys.stderr)


def still_tracked_note(unit: str) -> str:
    return (
        f"{unit} is generated but Git still tracks it, so it shows loadout's writes as "
        f"changes; untrack it and keep the files with `git rm -r --cached -- {shlex.quote(unit)}`"
    )


def _tracked_outputs(root: Path, written: Iterable[Path]) -> tuple[str, ...]:
    return still_tracked(root, generated_units(root, written))


def _sync_summary(outputs: Mapping[Path, Output], written: list[Path], retired: list[Path]) -> str:
    paths = {*outputs, *output_record_paths(outputs)}
    if not written and not retired:
        return "Up to date."
    summary = f"{len(written)} written, {len(paths) - len(written)} unchanged"
    return f"{summary}, {len(retired)} retired" if retired else summary


def _report_notices(root: Path, profile: str, context: RenderContext) -> None:
    """Advisory findings, on the same terms as a diverged vendored kit.

    A notice describes a source that rendered successfully while doing less than
    it says — a plugin left switched off, a hook that cannot fire. The output is
    correct, so this never moves an exit code; treating it as drift would fail a
    render that did the right thing with what it was given.

    Until this existed the four reports behind it reached nobody, and one was
    visible only by opening the generated JavaScript.
    """
    for notice in collect_notices(root, profile, context=context):
        print(f"note: {notice.render()}")


def _check_notes(root: Path, profile: str, context: RenderContext) -> list[str]:
    """Advisory findings, then each generated path Git still tracks: the files are as
    rendered, so that is a note rather than drift, as it is for sync."""
    state = sync_state(root)
    recorded = read_personal(root)
    kept = {*recorded.personal, *recorded.pending}
    tracked = (
        _tracked_outputs(root, [path for path in state.written if path not in kept])
        if state.project
        else ()
    )
    return [
        *(notice.render() for notice in collect_notices(root, profile, context=context)),
        *map(still_tracked_note, tracked),
    ]


UNMANAGED_ADVICE = (
    "not written by loadout, in a folder the harness reads whole; port it into the source "
    "with /loadout, then delete it"
)
UNMANAGED_LINK_ADVICE = (
    "a link loadout did not create, in a folder the harness reads whole; check where it "
    "points, then delete it"
)
DRIFT_ADVICE: dict[DriftKind, str] = {
    "source-changed": "the source changed since the last sync; run `{sync}`",
    "missing": "not on disk; run `{sync}`",
    "hand-edited": "edited outside loadout; port the edit into the source with /loadout, or run "
    "`{sync} --force` to discard it",
    "unknown": "no record of what loadout wrote here and no committed baseline, so a source "
    "change cannot be told from a hand edit; review the diff, then port what to keep into the "
    "source with /loadout, or run `{sync} --force` to discard it",
    "owned-key": "a key loadout writes in a file it shares was edited outside loadout; port "
    "the edit into the source with /loadout first, or run `{sync} --force` to discard it",
    "owned-key-removed": "a key loadout writes in a file it shares was removed outside loadout; "
    "remove it from the source with /loadout too, or run `{sync} --force` to write it back",
    "orphaned": "written by loadout but no longer rendered; run `{sync}` to retire it",
    "unmanaged": UNMANAGED_ADVICE,
    "unmanaged-link": UNMANAGED_LINK_ADVICE,
    "unparsable": "a file loadout shares with a harness no longer parses; fix or delete it, then "
    "run `{sync}`; for one the source still renders, `{sync} --force` replaces it",
}


def check_result(root: Path, profile: str) -> dict[str, Any]:
    context = RenderContext(registration=fixed(registration()), personal=sync_tier(root))
    drift = classified_drift(root, profile, context)
    notes = _check_notes(root, profile, context)
    return {
        "status": "drift" if drift else "clean",
        "exit_code": 1 if drift else 0,
        "drift": [{"path": str(d.path), "kind": d.kind} for d in drift],
        "notes": notes,
    }


def _check_json(root: Path, profile: str) -> int:
    result = check_result(root, profile)
    print(json.dumps(result, indent=2))
    return int(result["exit_code"])


def cmd_check(root: Path, profile: str, *, as_json: bool = False) -> int:
    if as_json:
        return _check_json(root, profile)
    # `check` used to end every drift with "run `loadout sync`" even where sync was
    # about to refuse, sending the reader in a circle and teaching them --force. Ask
    # the guard the same question sync will, and say which answer applies.
    context = RenderContext(registration=fixed(registration()), personal=sync_tier(root))
    drift = classified_drift(root, profile, context)
    for note in _check_notes(root, profile, context):
        print(f"note: {note}")
    if not drift:
        print_styled("generated files are up to date", GREEN, sys.stdout)
        return 0
    for item in drift:
        rel = _display(item.path, root)
        print_styled(f"DRIFT ({item.kind}): {rel}", BOLD_YELLOW, sys.stderr)
        if item.kind != "missing":
            report_diff(list(diff_lines(rel, item.actual, item.expected, context=3)))
    summary = drift_summary(drift)
    print(f"\n{summary[0]}", file=sys.stderr)
    for line in summary[1:]:
        print(line, file=sys.stderr)
    return 1


def drift_summary(
    drift: Sequence[Drift],
    sync: str = "loadout sync",
    advice: Mapping[DriftKind, str] = DRIFT_ADVICE,
) -> list[str]:
    lines = [f"{len(drift)} generated file(s) out of sync:"]
    for kind, template in advice.items():
        count = sum(1 for item in drift if item.kind == kind)
        if count:
            lines.append(f"  {count} {kind}: {template.format(sync=sync)}.")
    return lines


def _vendored_state(root: Path, name: str, recorded: str | None) -> str:
    """How a vendored copy stands against the hash recorded when it was vendored."""
    if recorded is None:
        return "no recorded hash"
    if tree_hash(vendored_path(root, name)) == recorded:
        return "clean"
    return "modified"


def cmd_kit_list(root: Path) -> int:
    contained(root)
    config = load_project_config(project_config_path(root))
    if not config.kits:
        print(
            f"no kits in this project — add one with `loadout kit add <name> --linked` "
            f"(or `--vendored`), or list it in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}"
        )
        return 0
    for name in config.kits:
        try:
            found = resolve_kit(name, root)
        except LoadoutError as error:
            # Reported rather than raised: `list` is the command you reach for
            # *because* something is wrong, so one broken name must not hide the rest.
            print(f"{name}: unresolved")
            print(f"    {error}")
            continue
        if found.kind == "vendored":
            print(f"{name}: vendored, {_vendored_state(root, name, config.vendored_hash(name))}")
        else:
            print(f"{name}: linked, from source {found.source}")
        print(f"    {_display(found.path, root)}")
    return 0


def cmd_kit_add(root: Path, name: str | None, mode: KitMode | None, *, yes: bool = False) -> int:
    chosen, code = _add_kit(root, name, mode, yes=yes)
    if code == 0:
        for skill in _identical_skills(root, chosen):
            print(identical_skill_note(chosen, skill))
    return code


def identical_skill_note(kit: str, skill: str) -> str:
    copy = f"{PROJECT_DIR}/{SKILLS_SUBDIR}/{skill}"
    return (
        f"{copy} is byte for byte the skill {kit} already supplies; while it is there it "
        f"replaces the kit's and stops following it, so delete it to take the "
        f"kit's: rm -r {copy}"
    )


def _identical_skills(root: Path, name: str) -> tuple[str, ...]:
    """The project skills `name` supplies with the same files and bytes."""
    path = resolve_kit(name, root).path
    offered = kit_skills(ProjectKits((path,), load_catalogs((path,))))
    return tuple(
        skill.name
        for skill in discover_skills(root / PROJECT_DIR / SKILLS_SUBDIR)
        if skill.name in offered and skill_bytes(skill) == skill_bytes(offered[skill.name])
    )


def skill_bytes(skill: Skill) -> dict[Path, bytes]:
    return {
        Path(SKILL_DOCUMENT): skill.document.read_bytes(),
        **{
            relative: (skill.supporting_root / relative).read_bytes()
            for relative in skill.supporting
        },
    }


def _add_kit(
    root: Path, name: str | None, mode: KitMode | None, *, yes: bool = False
) -> tuple[str, int]:
    """Choose a kit and how this project uses it, and the name chosen.

    Bare `add` enumerates every resolvable kit and prompts for a name and a
    mode; with a name, `--linked` or `--vendored` pins the mode for scripts.
    Adding a kit the project already lists changes its mode — the one path a config
    file edit cannot express, because vendoring copies a tree and records a hash.
    """
    if not sys.stdin.isatty():
        if name is None:
            raise UsageError(
                "`kit add` needs a name when not attached to a terminal: "
                "`loadout kit add <name> --linked` or `--vendored`"
            )
        if mode is None:
            raise UsageError(
                "`kit add` needs a mode when not attached to a terminal: "
                f"`loadout kit add {name} --linked` or `--vendored`"
            )
    contained(root)
    if name is None:
        name = _choose_kit(root)
    contained(root, name)
    if mode is None:
        mode = _choose_mode(name)

    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    is_vendored = local.exists()
    listed = name in config.kits

    if mode == "linked":
        if listed and is_vendored:
            return name, _make_linked(root, name)
        if is_vendored:
            raise LoadoutError(
                f"{name} has a vendored copy at {_display(local, root)} but is not "
                f"listed in `kits`; use `--vendored` to adopt it, or delete the copy first"
            )
        if listed:
            return name, _keep_linked(root, name, config.vendored_hash(name))
        found = resolve_kit(name, root)
        declare(root, name)
        print(f"linked {name} in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}")
        print(f"resolves from source {found.source}: {_display(found.path, root)}")
        return name, 0

    if is_vendored:
        if listed:
            raise LoadoutError(
                f"{name} is already vendored at {_display(local, root)}; run "
                f"`loadout kit sync {name}` to update it"
            )
        found = resolve_kit(name, root)
        declare(root, name)
        if config.vendored_hash(name) is None:
            record_hash(root, name, tree_hash(local))
        print(f"adopted the existing vendored copy of {name} at {_display(local, root)}")
        return name, 0
    return name, _make_vendored(root, name, yes=yes)


def _choose_kit(root: Path) -> str:
    # Never empty: the bundled kits are always on offer.
    return choose_kit(available_kits(root))


def choose_kit(options: tuple[AvailableKit, ...]) -> str:
    print("Available kits:")
    for index, option in enumerate(options, 1):
        # `(bundled)` and `(vendored)` come parenthesised; a source's name does not.
        label = option.source if option.kind != "source" else f"({option.source})"
        print(f"  {index}. {option.name}  {label}")
    span = f"1-{len(options)}"
    try:
        choice = input(f"Kit [{span}]: ").strip()
    except (EOFError, OSError):
        choice = ""
    # An explicit answer only: Enter or the end of input never picks one for the user.
    if not choice:
        raise UsageError(f"choose a kit by number ({span})")
    try:
        index = int(choice)
    except ValueError:
        raise UsageError(f"choose a kit by number ({span}), not {choice!r}") from None
    if not 1 <= index <= len(options):
        raise UsageError(f"no kit {index}; choose {span}")
    return options[index - 1].name


def _choose_mode(name: str) -> KitMode:
    try:
        choice = input(f"Use {name} as linked or vendored? [linked/vendored] ").strip().lower()
    except (EOFError, OSError):
        choice = ""
    modes: dict[str, KitMode] = {"linked": "linked", "vendored": "vendored"}
    if choice not in modes:
        raise UsageError(f"choose linked or vendored for {name}, not {choice!r}")
    return modes[choice]


def _make_vendored(root: Path, name: str, *, yes: bool = False) -> int:
    found = resolve_kit(name, root)
    local = vendored_path(root, name)
    if found.path.is_file():
        if vendor_catalog(root, name, found, yes=yes):
            print(f"vendored {name}; run `loadout sync` to regenerate project outputs")
        return 0
    copy_tree(found.path, local)
    declare(root, name)
    record_hash(root, name, tree_hash(local))
    print(f"vendored {name} from source {found.source} into {_display(local, root)}")
    for relative in kit_files(local):
        print(f"    {relative}")
    return 0


def _keep_linked(root: Path, name: str, recorded: str | None) -> int:
    found = resolve_kit(name, root)
    if recorded is not None:
        unrecord_hash(root, name)
    print(f"{name} is already linked; resolves from source {found.source}")
    print(f"    {_display(found.path, root)}")
    return 0


def _make_linked(root: Path, name: str) -> int:
    """A vendored copy becomes linked: drop the copy and its recorded hash once
    the source can still resolve the name, so the switch never strands the project."""
    found = resolve_upstream_kit(name)
    local = vendored_path(root, name)
    recorded = load_project_config(project_config_path(root)).vendored_hash(name)
    if has_local_edits(local, recorded, found.path):
        doomed = ", ".join(_display(path, root) for path in vendored_files(root, name, found.path))
        discard = f"delete {doomed} and run `loadout kit add {name} --linked`"
        if local.is_file():
            raise LoadoutError(
                f"{name}: vendored copy is modified or has no recorded provenance; switch to "
                f"linked refused. Keep it vendored, or {discard} to discard the edits"
            )
        _report_local_edits(name, local, found.path, unverifiable=recorded is None)
        print(
            f"\nSwitch to linked refused — the '-' lines above are in your copy, not the "
            f"source, and linking deletes the copy. To keep them, leave {name} vendored; "
            f"to discard them, {discard}.",
            file=sys.stderr,
        )
        return 1
    remove_vendored(root, name, found.path)
    unrecord_hash(root, name)
    print(f"changed {name} to linked; resolves from source {found.source}")
    print(f"    {_display(found.path, root)}")
    return 0


def _report_local_edits(name: str, local: Path, upstream: Path, *, unverifiable: bool) -> None:
    state = "has no recorded provenance" if unverifiable else "was modified after it was vendored"
    print(f"WARNING: the vendored copy of {name} {state}", file=sys.stderr)
    report_diff(kit_diff(local, upstream, name))


def cmd_kit_sync(root: Path, name: str, *, yes: bool = False) -> int:
    """Update a vendored copy from its source, refusing rather than merging."""
    contained(root, name)
    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    if local.is_file():
        if vendor_catalog(root, name, resolve_upstream_kit(name), update=True, yes=yes):
            print(f"updated {name}; run `loadout sync` to regenerate project outputs")
        return 0
    if not local.is_dir() and name not in config.kits:
        raise LoadoutError(
            f"{name} is not listed in loadout/config.toml, so there is nothing to sync; "
            f"run `loadout kit add {name}` to add it"
        )
    if not local.is_dir():
        raise LoadoutError(
            f"{name} is not vendored, so there is nothing to sync — it resolves from a "
            f"source on every render. Run `loadout kit add {name} --vendored` to copy it in."
        )

    update = update_vendored_directory(root, name)
    if update.state in {"edited", "unverifiable"}:
        _report_local_edits(
            name, local, update.upstream, unverifiable=update.state == "unverifiable"
        )
        if update.state == "unverifiable":
            print(
                f"\nSync refused — with no recorded hash, nothing can tell your edits from "
                f"the source's, so the '-' lines above may be either. Commit your copy first "
                f"if you want to keep them, or delete {_display(local, root)} and run "
                f"`loadout kit add {name} --vendored` to take the source wholesale.",
                file=sys.stderr,
            )
        else:
            print(
                f"\nSync refused — the '-' lines above exist only in your copy and would be "
                f"lost. Move them upstream, or delete {_display(local, root)} and run "
                f"`loadout kit add {name} --vendored` to take the upstream wholesale.",
                file=sys.stderr,
            )
        return 1
    if update.state == "current":
        print(f"{name} is up to date")
        return 0
    print(f"updated {name} from source, {len(update.changed)} file(s) changed")
    report_diff([line for diff in update.changed for line in diff])
    return 0
