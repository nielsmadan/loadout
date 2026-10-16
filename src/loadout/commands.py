from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from .atomic import atomic_install
from .bundled_skill import bundled_skill_path
from .diffs import diff_lines, report_diff
from .emit import RenderContext, refuse_unregistered_global, render_all
from .errors import LoadoutError, UsageError
from .files import DriftKind, refuse_unparsable
from .machine import fixed, registration, state_dir
from .notices import collect_notices
from .output import FrozenFile, Output, digests, output_record_paths
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    load_project_config,
    project_config_path,
)
from .skill_installation import (
    SkillSourceLocation,
    SourceSkillState,
    configured_skill_agents,
    inspect_skill_source,
    install_skill_source,
    uninstall_skill_source,
)
from .state import sync_state
from .sync import (
    classified_drift,
    deploy,
    newly_managed,
    refuse_unparsable_orphans,
    retire_orphans,
    unaccepted_outputs,
)
from .template_discover import available_templates
from .template_vendor import vendor_catalog
from .templates import (
    contained,
    copy_tree,
    declare,
    record_hash,
    remove_vendored,
    resolve_template,
    resolve_upstream_template,
    template_files,
    tree_hash,
    unrecord_hash,
    vendored_path,
)
from .unmanaged import unmanaged_entries
from .written import (
    prune_vanished,
)

TemplateMode = Literal["tracked", "vendored"]


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
    if _refused(location):
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


def _refused(location: SkillSourceLocation) -> bool:
    """Report a source copy loadout may not change, one it does not own or that was edited."""
    if location.state not in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
        return False
    detail = (
        "exists and is not owned by loadout"
        if location.state is SourceSkillState.CONFLICTING
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


def _abort_sentences(kinds: set[str]) -> list[str]:
    """What the refused diffs mean, by kind: a removed key shows as `+` lines sync would
    write back, anything else as `-` lines it would destroy (review 3, batch 2.11)."""
    sentences = []
    if kinds - {"owned-key-removed"}:
        sentences.append(
            "Sync aborted — the '-' lines would be lost. Port them into the source with "
            "/loadout, or run `loadout sync --force` to discard them."
        )
    if "owned-key-removed" in kinds:
        sentences.append(
            "Sync aborted — the '+' lines were removed outside loadout and would be written "
            "back. Remove them from the source with /loadout, or run `loadout sync --force` "
            "to write them back."
        )
    return sentences


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


def cmd_sync(root: Path, profile: str, force: bool = False) -> int:
    root = root.absolute()
    # One read of the registration decides the gate, the render and the guard.
    context = RenderContext(registration=fixed(registration()))
    refuse_unregistered_global(root, context)
    prune_vanished()
    # Rendered once and reused for the guard, the write and the record, so what is
    # recorded is provably what was written rather than a third render of it.
    outputs = render_all(root, profile, context)
    # The root as sync finds it, before writing anything.
    before = sync_state(root)
    refuse_unparsable_orphans(before, outputs)
    # Each copied source is hashed once a sync: by the guard, the writer and the record.
    sources = digests()
    if not force:
        refuse_unparsable(outputs)
        modified = unaccepted_outputs(before, outputs, sources, context)
        if modified:
            full: list[str] = []
            truncated = False
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
            for sentence in _abort_sentences({item.kind for item in modified}):
                print(f"\n{sentence}", file=sys.stderr)
            # A blocking prompt the reader cannot see all of is one they learn to
            # skip, and skipping this one means reaching for --force, which is the
            # thing it exists to prevent. Whatever the terminal truncates, the file
            # holds in full — the decision stays reviewable.
            if truncated and (kept := _write_full_diff(full)) is not None:
                print(f"\nThe complete diff is at {kept}", file=sys.stderr)
            return 1

    # Read before writing: once deployed, the value a key held is gone.
    managed = newly_managed(before, outputs)
    written, ignored = deploy(before, outputs, sources)
    for path in written:
        print(f"wrote {_display(path, root)}")
    for path, key, was in managed:
        print(f"now managed: {key} in {_display(path, root)} (was {was})")
    retired, unignored = retire_orphans(before, outputs)
    # Only the abort it described made the full diff worth keeping.
    with contextlib.suppress(OSError):
        (state_dir() / ABORT_DIFF).unlink(missing_ok=True)
    for path in retired:
        print(f"retired {_display(path, root)}")
    print(_sync_summary(outputs, written, retired))
    if ignored:
        print(f"added {ignored} entries to .gitignore")
    if unignored:
        print(f"removed {unignored} entries from .gitignore")
    _report_notices(root, profile)
    # Reported, never stopped on: sync overwrites nothing there, so stopping would
    # only block the outputs it does own.
    # The state as sync left it: the record from before names what was just retired
    # (review 5, batch 2.6).
    for path in unmanaged_entries(sync_state(root), outputs, profile):
        advice = UNMANAGED_LINK_ADVICE if path.is_symlink() else UNMANAGED_ADVICE
        print(f"WARNING: {_display(path, root)} is {advice}", file=sys.stderr)
    return 0


def _sync_summary(outputs: Mapping[Path, Output], written: list[Path], retired: list[Path]) -> str:
    paths = {*outputs, *output_record_paths(outputs)}
    if not written and not retired:
        return "Up to date."
    summary = f"{len(written)} written, {len(paths) - len(written)} unchanged"
    return f"{summary}, {len(retired)} retired" if retired else summary


def _report_notices(root: Path, profile: str) -> None:
    """Advisory findings, on the same terms as a diverged vendored template.

    A notice describes a source that rendered successfully while doing less than
    it says — a plugin left switched off, a hook that cannot fire. The output is
    correct, so this never moves an exit code; treating it as drift would fail a
    render that did the right thing with what it was given.

    Until this existed the four reports behind it reached nobody, and one was
    visible only by opening the generated JavaScript.
    """
    for notice in collect_notices(root, profile):
        print(f"note: {notice.render()}")


def _check_notes(root: Path, profile: str) -> list[str]:
    return [notice.render() for notice in collect_notices(root, profile)]


UNMANAGED_ADVICE = (
    "not written by loadout, in a folder the harness reads whole; port it into the source "
    "with /loadout, then delete it"
)
UNMANAGED_LINK_ADVICE = (
    "a link loadout did not create, in a folder the harness reads whole; check where it "
    "points, then delete it"
)
DRIFT_ADVICE: dict[DriftKind, str] = {
    "source-changed": "the source changed since the last sync; run `loadout sync`",
    "missing": "not on disk; run `loadout sync`",
    "hand-edited": "edited outside loadout; port the edit into the source with /loadout, or run "
    "`loadout sync --force` to discard it",
    "unknown": "no record of what loadout wrote here and no committed baseline, so a source "
    "change cannot be told from a hand edit; review the diff, then port what to keep into the "
    "source with /loadout, or run `loadout sync --force` to discard it",
    "owned-key": "a key loadout writes in a file it shares was edited outside loadout; port "
    "the edit into the source with /loadout first, or run `loadout sync --force` to discard it",
    "owned-key-removed": "a key loadout writes in a file it shares was removed outside loadout; "
    "remove it from the source with /loadout too, or run `loadout sync --force` to write it back",
    "orphaned": "written by loadout but no longer rendered; run `loadout sync` to retire it",
    "unmanaged": UNMANAGED_ADVICE,
    "unmanaged-link": UNMANAGED_LINK_ADVICE,
    "unparsable": "a file loadout shares with a harness no longer parses; fix or delete it, then "
    "run `loadout sync`; for one the source still renders, `loadout sync --force` replaces it",
}


def _check_json(root: Path, profile: str) -> int:
    drift = classified_drift(root, profile, RenderContext(registration=fixed(registration())))
    notes = _check_notes(root, profile)
    code = 1 if drift else 0
    result = {
        "status": "drift" if drift else "clean",
        "exit_code": code,
        "drift": [{"path": str(d.path), "kind": d.kind} for d in drift],
        "notes": notes,
    }
    print(json.dumps(result, indent=2))
    return code


def cmd_check(root: Path, profile: str, *, as_json: bool = False) -> int:
    if as_json:
        return _check_json(root, profile)
    # `check` used to end every drift with "run `loadout sync`" even where sync was
    # about to refuse, sending the reader in a circle and teaching them --force. Ask
    # the guard the same question sync will, and say which answer applies.
    drift = classified_drift(root, profile, RenderContext(registration=fixed(registration())))
    for note in _check_notes(root, profile):
        print(f"note: {note}")
    if not drift:
        print("generated files are up to date")
        return 0
    for item in drift:
        rel = _display(item.path, root)
        print(f"DRIFT ({item.kind}): {rel}", file=sys.stderr)
        report_diff(list(diff_lines(rel, item.actual, item.expected, context=3)))
    print(f"\n{len(drift)} generated file(s) out of sync:", file=sys.stderr)
    for kind, advice in DRIFT_ADVICE.items():
        count = sum(1 for item in drift if item.kind == kind)
        if count:
            print(f"  {count} {kind}: {advice}.", file=sys.stderr)
    return 1


def _vendored_state(root: Path, name: str, recorded: str | None) -> str:
    """How a vendored copy stands against the hash recorded when it was vendored."""
    if recorded is None:
        return "no recorded hash"
    if tree_hash(vendored_path(root, name)) == recorded:
        return "clean"
    return "modified"


def cmd_template_list(root: Path) -> int:
    contained(root)
    config = load_project_config(project_config_path(root))
    if not config.templates:
        print(
            f"no templates declared — add one with `loadout template add <name> --tracked` "
            f"(or `--vendored`), or list it in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}"
        )
        return 0
    for name in config.templates:
        try:
            found = resolve_template(name, root)
        except LoadoutError as error:
            # Reported rather than raised: `list` is the command you reach for
            # *because* something is wrong, so one broken name must not hide the rest.
            print(f"{name}: unresolved")
            print(f"    {error}")
            continue
        if found.kind == "vendored":
            print(f"{name}: vendored, {_vendored_state(root, name, config.vendored_hash(name))}")
        else:
            print(f"{name}: tracked, from source {found.source}")
        print(f"    {_display(found.path, root)}")
    return 0


def cmd_template_add(
    root: Path, name: str | None, mode: TemplateMode | None, *, yes: bool = False
) -> int:
    """Choose a template and how this project uses it.

    Bare `add` enumerates every resolvable template and prompts for a name and a
    mode; with a name, `--tracked` or `--vendored` pins the mode for scripts.
    Adding an already-declared template changes its mode — the one path a config
    file edit cannot express, because vendoring copies a tree and records a hash.
    """
    if not sys.stdin.isatty():
        if name is None:
            raise UsageError(
                "`template add` needs a name when not attached to a terminal: "
                "`loadout template add <name> --tracked` or `--vendored`"
            )
        if mode is None:
            raise UsageError(
                "`template add` needs a mode when not attached to a terminal: "
                f"`loadout template add {name} --tracked` or `--vendored`"
            )
    contained(root)
    if name is None:
        name = _choose_template(root)
    contained(root, name)
    if mode is None:
        mode = _choose_mode(name)

    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    is_vendored = local.exists()
    declared = name in config.templates

    if mode == "tracked":
        if declared and is_vendored:
            return _make_tracked(root, name)
        if is_vendored:
            raise LoadoutError(
                f"{name} has a vendored copy at {_display(local, root)} but is not "
                f"declared; use `--vendored` to adopt it, or delete the copy first"
            )
        if declared:
            found = resolve_template(name, root)
            print(f"{name} is already tracked; resolves from source {found.source}")
            print(f"    {_display(found.path, root)}")
            return 0
        found = resolve_template(name, root)
        declare(root, name)
        print(f"declared {name} in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}")
        print(f"resolves from source {found.source}: {_display(found.path, root)}")
        return 0

    if is_vendored:
        if declared:
            raise LoadoutError(
                f"{name} is already vendored at {_display(local, root)}; run "
                f"`loadout template sync {name}` to update it"
            )
        found = resolve_template(name, root)
        declare(root, name)
        if config.vendored_hash(name) is None:
            record_hash(root, name, tree_hash(local))
        print(f"adopted the existing vendored copy of {name} at {_display(local, root)}")
        return 0
    return _make_vendored(root, name, yes=yes)


def _choose_template(root: Path) -> str:
    # Never empty: the bundled starters are always on offer.
    options = available_templates(root)
    print("Available templates:")
    for index, option in enumerate(options, 1):
        # `(bundled)` and `(vendored)` come parenthesised; a source's name does not.
        label = option.source if option.kind != "source" else f"({option.source})"
        print(f"  {index}. {option.name}  {label}")
    span = f"1-{len(options)}"
    try:
        choice = input(f"Template [{span}]: ").strip()
    except (EOFError, OSError):
        choice = ""
    # An explicit answer only: Enter or the end of input never picks one for the user.
    if not choice:
        raise UsageError(f"choose a template by number ({span})")
    try:
        index = int(choice)
    except ValueError:
        raise UsageError(f"choose a template by number ({span}), not {choice!r}") from None
    if not 1 <= index <= len(options):
        raise UsageError(f"no template {index}; choose {span}")
    return options[index - 1].name


def _choose_mode(name: str) -> TemplateMode:
    try:
        choice = input(f"Use {name} as tracked or vendored? [tracked/vendored] ").strip().lower()
    except (EOFError, OSError):
        choice = ""
    modes: dict[str, TemplateMode] = {"tracked": "tracked", "vendored": "vendored"}
    if choice not in modes:
        raise UsageError(f"choose tracked or vendored for {name}, not {choice!r}")
    return modes[choice]


def _make_vendored(root: Path, name: str, *, yes: bool = False) -> int:
    found = resolve_template(name, root)
    local = vendored_path(root, name)
    if found.path.is_file():
        return vendor_catalog(root, name, found, yes=yes)
    copy_tree(found.path, local)
    declare(root, name)
    record_hash(root, name, tree_hash(local))
    print(f"vendored {name} from source {found.source} into {_display(local, root)}")
    for relative in template_files(local):
        print(f"    {relative}")
    return 0


def _make_tracked(root: Path, name: str) -> int:
    """A vendored copy becomes tracked: drop the copy and its recorded hash once
    the source can still resolve the name, so the switch never strands the project."""
    found = resolve_upstream_template(name)
    remove_vendored(root, name)
    unrecord_hash(root, name)
    print(f"changed {name} to tracked; resolves from source {found.source}")
    print(f"    {_display(found.path, root)}")
    return 0


def _tree_diff(local: Path, upstream: Path, label: str) -> list[str]:
    """A unified diff per file across both trees, added and removed files included."""
    lines: list[str] = []
    for relative in sorted({*template_files(local), *template_files(upstream)}):
        here, there = local / relative, upstream / relative
        try:
            actual = here.read_text(encoding="utf-8") if here.is_file() else ""
            expected = there.read_text(encoding="utf-8") if there.is_file() else ""
        except UnicodeDecodeError:
            # A template carries whatever a skill carries, binaries included, and
            # a byte diff of one is noise rather than a review.
            if not (here.is_file() and there.is_file()) or here.read_bytes() != there.read_bytes():
                lines.append(f"{label}/{relative}: differs (binary)\n")
            continue
        if actual != expected:
            lines.extend(diff_lines(f"{label}/{relative}", actual, expected, context=3))
    return lines


def cmd_template_sync(root: Path, name: str, *, yes: bool = False) -> int:
    """Update a vendored copy from its source, refusing rather than merging.

    Refuse-and-diff is never wrong and never silently mangles anything. The
    alternative — a three-way merge against the version you vendored — is the
    largest single piece of work in this design, and the recorded hash is exactly
    the base it would need, so deferring it costs no state migration.
    """
    contained(root, name)
    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    if local.is_file():
        return vendor_catalog(root, name, resolve_upstream_template(name), update=True, yes=yes)
    if not local.is_dir() and name not in config.templates:
        raise LoadoutError(
            f"{name} is not declared in loadout/config.toml, so there is nothing to sync; "
            f"run `loadout template add {name}` to add it"
        )
    if not local.is_dir():
        raise LoadoutError(
            f"{name} is not vendored, so there is nothing to sync — it resolves from a "
            f"source on every render. Run `loadout template add {name} --vendored` to copy it in."
        )

    recorded = config.vendored_hash(name)
    # A vendored copy resolves ahead of every source, so the upstream has to be
    # reached past it deliberately.
    found = resolve_upstream_template(name)
    upstream = found.path
    if upstream.is_file():
        raise LoadoutError("template sync cannot change between a directory and a catalog manifest")
    current = tree_hash(local)

    # Refuse unless the copy can be *proved* unmodified, rather than refusing only
    # when it can be proved modified. Those differ exactly when there is no
    # provenance to compare against — which is the state `harness add` produced —
    # and fail-open there would let a command whose contract is refuse-rather-than-
    # merge silently overwrite local edits. Matching upstream is proof enough, so a
    # clean copy still falls through to the self-heal below and is never refused.
    if recorded is None and tree_hash(upstream) != current:
        print(
            f"WARNING: the vendored copy of {name} has no recorded provenance",
            file=sys.stderr,
        )
        report_diff(_tree_diff(local, upstream, name))
        print(
            f"\nSync refused — with no recorded hash, nothing can tell your edits from "
            f"the source's, so the '-' lines above may be either. Commit your copy first "
            f"if you want to keep them, or delete {_display(local, root)} and run "
            f"`loadout template add {name} --vendored` to take the source wholesale.",
            file=sys.stderr,
        )
        return 1

    if recorded is not None and current != recorded:
        print(
            f"WARNING: the vendored copy of {name} was modified after it was vendored",
            file=sys.stderr,
        )
        report_diff(_tree_diff(local, upstream, name))
        print(
            f"\nSync refused — the '-' lines above exist only in your copy and would be "
            f"lost. Move them upstream, or delete {_display(local, root)} and run "
            f"`loadout template add {name} --vendored` to take the upstream wholesale.",
            file=sys.stderr,
        )
        return 1

    # **Position is deliberate: this must stay below both refusals.** It reads
    # like a cheap early-out that belongs at the top, and hoisting it changes a
    # real case — a copy whose recorded hash says modified but whose content now
    # equals the upstream currently refuses, and would start reporting "up to
    # date" instead. Nothing asked for that. The no-provenance gate above already
    # falls through to here when the copy matches, which is the self-heal.
    if tree_hash(upstream) == current:
        print(f"{name} is up to date")
        if recorded is None:
            record_hash(root, name, current)
        return 0

    changed = _tree_diff(local, upstream, name)
    copy_tree(upstream, local)
    record_hash(root, name, tree_hash(local))
    print(
        f"updated {name} from source, {sum(1 for c in changed if c.startswith('---'))} file(s) changed"
    )
    report_diff(changed)
    return 0
