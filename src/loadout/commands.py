from __future__ import annotations

import difflib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, TextIO

from .bundled_skill import bundled_skill_path
from .emit import Merged, Output, render_all
from .errors import LoadoutError, UsageError
from .migration_backup import state_root
from .notices import collect_notices
from .output import FrozenFile, atomic_install
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
from .sync import classified_drift, deploy, retire_orphans, unaccepted_outputs
from .template_discover import available_templates
from .template_vendor import vendor_catalog
from .templates import (
    VENDORED,
    copy_tree,
    declare,
    record_hash,
    remove_vendored,
    resolve_template,
    resolve_upstream_template,
    template_divergence,
    template_files,
    tree_hash,
    unrecord_hash,
    unverifiable_templates,
    vendored_path,
)
from .unmanaged import unmanaged_entries
from .written import (
    prune_vanished,
)

DIFF_LIMIT = 40
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


def _write_full_diff(lines: list[str]) -> Path:
    """The untruncated diff, so an abort the terminal shortened stays reviewable."""
    path = state_root() / "sync-abort.diff"
    atomic_install(path, FrozenFile("".join(lines).encode("utf-8"), 0o600), path.parent)
    return path


def diff_lines(rel: str, actual: str, expected: str, context: int) -> list[str]:
    return list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{rel} (on disk)",
            tofile=f"{rel} (expected)",
            n=context,
        )
    )


def cmd_sync(root: Path, profile: str = "default", force: bool = False) -> int:
    root = root.absolute()
    prune_vanished()
    # Rendered once and reused for the guard, the write and the record, so what is
    # recorded is provably what was written rather than a third render of it.
    outputs = render_all(root, profile)
    if not force:
        modified = unaccepted_outputs(root, outputs)
        if modified:
            full: list[str] = []
            truncated = False
            for item in modified:
                rel = _display(item.path, root)
                state = " and is no longer rendered" if item.orphaned else ""
                print(f"WARNING: {rel} was modified outside loadout{state}", file=sys.stderr)
                # Tight context: on a 16k settings.json the one runtime-added entry
                # should be readable without scrolling past the whole document.
                lines = diff_lines(rel, item.actual, item.expected, context=1)
                full.extend(lines)
                truncated = report_diff(lines) or truncated
            print(
                "\nSync aborted — the '-' lines would be lost. Port them into the source "
                "with /loadout, or run `loadout sync --force` to discard them.",
                file=sys.stderr,
            )
            # A blocking prompt the reader cannot see all of is one they learn to
            # skip, and skipping this one means reaching for --force, which is the
            # thing it exists to prevent. Whatever the terminal truncates, the file
            # holds in full — the decision stays reviewable.
            if truncated:
                print(f"\nThe complete diff is at {_write_full_diff(full)}", file=sys.stderr)
            return 1

    written, ignored = deploy(root, outputs)
    for path in written:
        action = "wrote" if path.exists() else "retired"
        print(f"{action} {_display(path, root)}")
    retired, unignored = retire_orphans(root, outputs)
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
    for path in unmanaged_entries(root, outputs, profile):
        print(f"WARNING: {_display(path, root)} is {UNMANAGED_ADVICE}", file=sys.stderr)
    return 0


def _sync_summary(outputs: Mapping[Path, Output], written: list[Path], retired: list[Path]) -> str:
    paths = set(outputs) | {
        record
        for content in outputs.values()
        if isinstance(content, Merged)
        for record, _ in content.records
    }
    changed = sum(1 for path in written if path in paths)
    if not changed and not retired:
        return "Up to date."
    summary = f"{changed} written, {len(paths) - changed} unchanged"
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
    notes = [notice.render() for notice in collect_notices(root, profile)]
    # Reported, never failed: check's contract is that generated output matches
    # its source, and a vendored template *is* source — so it falls outside that
    # jurisdiction by definition rather than by exemption (ADR 0014). Saying
    # nothing at all would let a project drift from its template indefinitely,
    # which is the failure vendoring exists to avoid.
    notes += [
        f"the vendored template {name!r} was modified after it was vendored — "
        f"`loadout template sync {name}` will refuse until those edits go upstream"
        for name in template_divergence(root)
    ]
    notes += [
        f"the vendored template {name!r} has no recorded provenance, so nothing "
        f"can tell your edits from its source — `loadout template add {name} --vendored` takes "
        f"the source wholesale and records one"
        for name in unverifiable_templates(root)
    ]
    return notes


UNMANAGED_ADVICE = (
    "not written by loadout, in a folder the harness reads whole; port it into the source "
    "with /loadout, then delete it"
)
DRIFT_ADVICE = {
    "source-changed": "the source changed since the last sync; run `loadout sync`",
    "missing": "not on disk; run `loadout sync`",
    "hand-edited": "edited outside loadout; port the edit into the source with /loadout, or run "
    "`loadout sync --force` to discard it",
    "unknown": "no record of what loadout wrote here and no committed baseline, so a source "
    "change cannot be told from a hand edit; review the diff, then port what to keep into the "
    "source with /loadout, or run `loadout sync --force` to discard it",
    "orphaned": "written by loadout but no longer rendered; run `loadout sync` to retire it",
    "unmanaged": UNMANAGED_ADVICE,
}


def _check_json(root: Path, profile: str) -> int:
    drift = classified_drift(root, profile)
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


def cmd_check(root: Path, profile: str = "default", *, as_json: bool = False) -> int:
    if as_json:
        return _check_json(root, profile)
    # `check` used to end every drift with "run `loadout sync`" even where sync was
    # about to refuse, sending the reader in a circle and teaching them --force. Ask
    # the guard the same question sync will, and say which answer applies.
    drift = classified_drift(root, profile)
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
        if found.source == VENDORED:
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
    if mode is None and not sys.stdin.isatty():
        raise LoadoutError(
            "`template add` needs a mode when not attached to a terminal: "
            f"`loadout template add {name or '<name>'} --tracked` or `--vendored`"
        )
    if name is None:
        name = _choose_template(root)
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
    options = available_templates(root)
    if not options:
        raise LoadoutError(
            "no templates available: nothing is vendored here and no source this "
            "machine declares offers one"
        )
    print("Available templates:")
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option.name}  ({option.source})")
    try:
        choice = input(f"Template [1-{len(options)}]: ").strip()
    except (EOFError, OSError):
        choice = ""
    if not choice:
        return options[0].name
    try:
        index = int(choice)
    except ValueError:
        raise LoadoutError(f"choose a template by number, not {choice!r}") from None
    if not 1 <= index <= len(options):
        raise LoadoutError(f"no template {index}; choose 1-{len(options)}")
    return options[index - 1].name


def _choose_mode(name: str) -> TemplateMode:
    try:
        choice = input(f"Use {name} as tracked or vendored? [tracked/vendored] ").strip().lower()
    except (EOFError, OSError):
        choice = ""
    modes: dict[str, TemplateMode] = {"tracked": "tracked", "vendored": "vendored"}
    if choice not in modes:
        raise LoadoutError(f"choose tracked or vendored for {name}, not {choice!r}")
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


def report_diff(lines: list[str], stream: TextIO | None = None) -> bool:
    """Show at most DIFF_LIMIT lines of a diff, on stderr unless told otherwise; True
    when some were left out."""
    stream = stream or sys.stderr
    stream.writelines(lines[:DIFF_LIMIT])
    if len(lines) <= DIFF_LIMIT:
        return False
    print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=stream)
    return True


def cmd_template_sync(root: Path, name: str, *, yes: bool = False) -> int:
    """Update a vendored copy from its source, refusing rather than merging.

    Refuse-and-diff is never wrong and never silently mangles anything. The
    alternative — a three-way merge against the version you vendored — is the
    largest single piece of work in this design, and the recorded hash is exactly
    the base it would need, so deferring it costs no state migration.
    """
    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    if local.is_file():
        return vendor_catalog(root, name, resolve_upstream_template(name), update=True, yes=yes)
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
