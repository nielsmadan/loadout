from __future__ import annotations

import contextlib
import difflib
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .bundled_skill import bundled_skill_path
from .deployment import DeploymentConflict, managed_paths
from .emit import (
    Copied,
    Merged,
    Output,
    artifact_deployment_scopes,
    check_all,
    collect_notices,
    created_document,
    declared_profiles,
    describe_file,
    render_all,
    write_outputs,
)
from .errors import LoadoutError, UsageError
from .machine import machine_config_path
from .output import FrozenFile
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    gitignore_entries,
    load_project_config,
    project_config_path,
)
from .retirement import orphan_drift, orphaned, retire
from .scaffold import _append_gitignore, _remove_gitignore, init_global, init_project
from .skill_installation import (
    SkillSourceLocation,
    SourceSkillState,
    configured_skill_agents,
    inspect_skill_source,
    install_skill_source,
    uninstall_skill_source,
)
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
from .written import (
    WrittenEntry,
    accepts_bytes,
    accepts_text,
    copied_entry,
    frozen_entry,
    merged_entry,
    normalise,
    prune_vanished,
    read_written,
    record_written,
    text_entry,
)

DIFF_LIMIT = 40


def cmd_init(root: Path, harnesses: tuple[str, ...]) -> int:
    for action in init_project(root, harnesses, machine_config_path=machine_config_path()):
        print(action)
    print("\nEdit loadout/permissions.toml, then run `loadout sync`.")
    return 0


def cmd_init_global(source: Path | None, force: bool = False) -> int:
    if source is None:
        if not sys.stdin.isatty():
            raise LoadoutError(
                "a directory is required when not attached to a terminal "
                "(loadout init global <path>)"
            )
        default = Path.home() / "loadout"
        response = input(f"Directory to hold the global loadout source [{default}]: ").strip()
        source = Path(response) if response else default
    for action in init_global(source.expanduser(), machine_config_path(), force=force):
        print(action)
    return 0


def _print_skill_location(
    root: Path, profile: str, source_name: str | None
) -> tuple[Path, SkillSourceLocation, tuple[str, ...]]:
    bundle = bundled_skill_path()
    location = inspect_skill_source(root, profile, bundle, source_name)
    agents = configured_skill_agents(root, profile)
    print(f"source {location.source}: {location.path} ({location.state.value})")
    print(f"configured agents: {', '.join(agents) if agents else 'none'}")
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
        print("configured agents: none")
        print("no configured agents receive global skills; no changes made")
        return 0
    bundle, location, _ = _print_skill_location(root, profile, source_name)
    if location.state in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
        detail = (
            "exists and is not owned by loadout"
            if location.state is SourceSkillState.CONFLICTING
            else "was modified after installation"
        )
        print(f"loadout: {location.path} {detail}; no changes made", file=sys.stderr)
        return 1
    if location.state is not SourceSkillState.INSTALLED and not _confirm_skill_change(
        "install", location.path, yes
    ):
        print("declined; no changes made")
        return 0
    try:
        changed = install_skill_source(location, bundle)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 1
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
    if location.state in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
        detail = (
            "exists and is not owned by loadout"
            if location.state is SourceSkillState.CONFLICTING
            else "was modified after installation"
        )
        print(f"loadout: {location.path} {detail}; no changes made", file=sys.stderr)
        return 1
    if not _confirm_skill_change("uninstall", location.path, yes):
        print("declined; no changes made")
        return 0
    try:
        removed = uninstall_skill_source(location, root=root, profile=profile)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 1
    print(
        f"uninstalled loadout skill from source {location.source}: {location.path} "
        f"({len(removed)} generated files removed)"
    )
    return cmd_sync(root, profile=profile)


def _display(path: Path, root: Path) -> str:
    """A destination may live outside root (e.g. under ~); relative_to() would raise."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _render_variants(
    source_root: Path, profiles: Iterable[str], rebase_to: Path
) -> dict[Path, set[str]]:
    variants: dict[Path, set[str]] = {}
    for profile in profiles:
        try:
            rendered = render_all(source_root, profile)
        except LoadoutError:
            continue
        for path, content in rendered.items():
            if isinstance(content, FrozenFile):
                continue
            if isinstance(content, Copied):
                continue  # verbatim by definition, so it has no per-profile form to vary
            if isinstance(content, Merged):
                continue  # its legitimate content includes foreign material, so the
                # set of acceptable forms is not finite and cannot be enumerated here
            try:
                key = rebase_to / path.relative_to(source_root)
            except ValueError:
                key = path  # a destination outside the root renders to the same path
            variants.setdefault(key, set()).add(normalise(key, content))
    return variants


def _committed_variants(root: Path, profiles: Iterable[str]) -> dict[Path, set[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "archive", "HEAD"], capture_output=True, check=False
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
                tar.extractall(path=tmp_dir, filter="data")
        except (tarfile.TarError, OSError):
            return {}
        return _render_variants(Path(tmp_dir), profiles, rebase_to=root)


@dataclass(frozen=True)
class Unaccepted:
    """A file on disk no baseline accounts for. `kind` is hand-edited when a commit or
    a record says what loadout wrote there, and unknown when neither exists. `orphaned`
    marks a recorded destination this run no longer renders, which sync would retire."""

    path: Path
    kind: str
    actual: str
    expected: str
    orphaned: bool = False


def _modified_outside_loadout(
    root: Path, outputs: Mapping[Path, Output], profile: str = "default"
) -> list[tuple[Path, str, str]]:
    return [(u.path, u.actual, u.expected) for u in unaccepted_outputs(root, outputs, profile)]


def _text_accepted(path: Path, actual: str, forms: set[str], entry: WrittenEntry | None) -> bool:
    return normalise(path, actual) in forms or accepts_text(entry, path, actual)


def _bytes_accepted(data: bytes, expected: bytes | None, entry: WrittenEntry | None) -> bool:
    return data == expected or accepts_bytes(entry, data)


def unaccepted_outputs(
    root: Path, outputs: Mapping[Path, Output], profile: str = "default"
) -> list[Unaccepted]:
    """Files matching no output loadout itself could have written.

    The one classifier behind sync's refusal, check's drift kinds and retirement, so
    none of them can disagree about what counts as a hand edit.

    The accept set spans the committed source, the working source, every declared
    profile and the record of what sync last wrote, so an unsynced source edit, a
    synced one and a profile switch all match something. With neither a commit nor a
    record, only the working source remains: a file that does not match it is content
    loadout did not write, and is refused rather than overwritten.

    A recorded destination this run no longer renders is judged the same way, with
    deletion as the change, so an edit made to it after sync wrote it is refused too.
    """
    profiles = declared_profiles(root)
    committed = _committed_variants(root, profiles)
    acceptable = {path: set(forms) for path, forms in committed.items()}
    for path, forms in _render_variants(root, profiles, rebase_to=root).items():
        acceptable.setdefault(path, set()).update(forms)
    # The third variant, and the only one that knows about a source state which was
    # never committed and has since moved on. See written.py and ADR 0019.
    written = read_written(root)

    def unaccepted(path: Path, actual: str, expected: str) -> Unaccepted:
        baseline = path in committed or written.get(path) is not None
        return Unaccepted(path, "hand-edited" if baseline else "unknown", actual, expected)

    modified: list[Unaccepted] = []
    artifacts = managed_paths(artifact_deployment_scopes(root, profile), outputs)
    for path, expected in outputs.items():
        if path in artifacts:
            continue
        if isinstance(expected, FrozenFile):
            if path.is_file() and not _bytes_accepted(
                path.read_bytes(), expected.content, written.get(path)
            ):
                modified.append(
                    unaccepted(path, describe_file(path), f"{len(expected.content)} bytes\n")
                )
            continue
        if isinstance(expected, Copied):
            # A copied file has no per-profile form, so it is compared against its
            # source directly. check_all only *reports* drift; this is the guard
            # that stops sync overwriting a hand edit, and it has to cover a
            # supporting file just as much as a SKILL.md.
            if path.is_file() and not _bytes_accepted(
                path.read_bytes(), expected.read_bytes(), written.get(path)
            ):
                modified.append(
                    unaccepted(
                        path, describe_file(path), describe_file(expected.source, expected.prefix)
                    )
                )
            continue
        if isinstance(expected, Merged):
            # Applying replaces owned keys and passes everything else through, so
            # sync cannot destroy a foreign edit here and there is nothing for this
            # guard to protect. An edit to an *owned* key is still overwritten
            # silently; `check` reports it, and covering it properly needs a
            # baseline this guard does not have for a file loadout does not own.
            continue
        forms = acceptable.get(path, set())
        if not path.is_file() or not forms:
            continue
        actual = path.read_text(encoding="utf-8")
        if not _text_accepted(path, actual, forms, written.get(path)):
            modified.append(unaccepted(path, actual, expected))
    modified += _unaccepted_orphans(orphaned(written, outputs), acceptable)
    return modified


def _unaccepted_orphans(
    orphans: Mapping[Path, WrittenEntry], acceptable: Mapping[Path, set[str]]
) -> list[Unaccepted]:
    modified: list[Unaccepted] = []
    for path, entry in sorted(orphans.items()):
        # A merged orphan loses only loadout's keys, which the guard never protects.
        if entry.kind == "merged" or not path.is_file():
            continue
        if entry.kind == "text":
            actual = path.read_text(encoding="utf-8")
            if not _text_accepted(path, actual, acceptable.get(path, set()), entry):
                modified.append(Unaccepted(path, "hand-edited", actual, "", orphaned=True))
        elif not _bytes_accepted(path.read_bytes(), None, entry):
            modified.append(
                Unaccepted(path, "hand-edited", describe_file(path), "(absent)\n", orphaned=True)
            )
    return modified


def _write_full_diff(lines: list[str]) -> Path:
    """The untruncated diff, so an abort the terminal shortened stays reviewable."""
    descriptor, name = tempfile.mkstemp(suffix=".diff", prefix="loadout-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    return Path(name)


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
        modified = unaccepted_outputs(root, outputs, profile)
        if modified:
            full: list[str] = []
            for item in modified:
                rel = _display(item.path, root)
                state = " and is no longer rendered" if item.orphaned else ""
                print(f"WARNING: {rel} was modified outside loadout{state}", file=sys.stderr)
                # Tight context: on a 16k settings.json the one runtime-added entry
                # should be readable without scrolling past the whole document.
                lines = diff_lines(rel, item.actual, item.expected, context=1)
                full.extend(lines)
                sys.stderr.writelines(lines[:DIFF_LIMIT])
                if len(lines) > DIFF_LIMIT:
                    print(f"    ... {len(lines) - DIFF_LIMIT} more line(s)", file=sys.stderr)
            print(
                "\nSync aborted — the '-' lines would be lost. Move them into the source, "
                "or run `loadout sync --force` to discard them.",
                file=sys.stderr,
            )
            # A blocking prompt the reader cannot see all of is one they learn to
            # skip, and skipping this one means reaching for --force, which is the
            # thing it exists to prevent. Whatever the terminal truncates, the file
            # holds in full — the decision stays reviewable.
            if any(
                len(diff_lines(_display(u.path, root), u.actual, u.expected, context=1))
                > DIFF_LIMIT
                for u in modified
            ):
                print(f"\nThe complete diff is at {_write_full_diff(full)}", file=sys.stderr)
            return 1

    try:
        written, ignored = deploy(root, profile, outputs, force=force)
    except DeploymentConflict as error:
        print(f"Sync aborted: {error}", file=sys.stderr)
        return 1
    for path in written:
        action = "wrote" if path.exists() else "retired"
        print(f"{action} {_display(path, root)}")
    retired, unignored = retire_orphans(root, profile, outputs)
    for path in retired:
        print(f"retired {_display(path, root)}")
    print(_sync_summary(outputs, written, retired))
    if ignored:
        print(f"added {ignored} entries to .gitignore")
    if unignored:
        print(f"removed {unignored} entries from .gitignore")
    _report_notices(root, profile)
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


def retire_orphans(
    root: Path, profile: str, outputs: Mapping[Path, Output]
) -> tuple[list[Path], int]:
    """Retire every recorded destination this run no longer renders, once the guard
    has passed or been forced, and forget them. Returns the paths changed on disk and
    how many `.gitignore` entries were dropped."""
    written = read_written(root)
    orphans = orphaned(written, outputs)
    if not orphans:
        return [], 0
    retired = retire(orphans, written)
    record_written(root, profile, {}, dropped=orphans)
    if not project_config_path(root).is_file():
        return retired, 0
    deleted = [path for path in retired if orphans[path].kind != "merged"]
    unwanted = set(gitignore_entries(root, deleted)) - set(gitignore_entries(root, outputs))
    return retired, _remove_gitignore(root, unwanted)


def deploy(
    root: Path, profile: str, outputs: Mapping[Path, Output], *, force: bool = False
) -> tuple[list[Path], int]:
    """Write rendered outputs, record them, and ignore them: what sync does once the
    guard has passed, shared with init's first render. Returns the paths written and
    how many `.gitignore` entries were added."""
    written = write_outputs(outputs, scopes=artifact_deployment_scopes(root, profile), force=force)
    record_written(root, profile, _entries_for(outputs))
    if not project_config_path(root).is_file():
        return written, 0
    # Root-anchored, the form init's migration writes: a bare "CLAUDE.md"
    # would also ignore loadout/instructions/native/claude/CLAUDE.md, and
    # `git add loadout` then silently drops that source from the index.
    entries = list(gitignore_entries(root, outputs))
    return written, len(entries) if _append_gitignore(root, entries) else 0


@dataclass(frozen=True)
class RenderedChange:
    """One output against the file on disk. `kind` is identical, update or create;
    `actual` and `wanted` are text for a diff, or a size and digest for a copy."""

    path: Path
    kind: str
    actual: str
    wanted: str


def _copy_summary(content: bytes, mode: int) -> str:
    return f"{len(content)} bytes, mode {mode & 0o777:o}, sha256 {hashlib.sha256(content).hexdigest()}\n"


def compare_with_disk(outputs: Mapping[Path, Output]) -> list[RenderedChange]:
    """What writing `outputs` would do, judged the way the guard judges a change:
    equal under `normalise` is identical, so a banner or a JSON re-serialisation is
    not a difference."""
    texts: dict[Path, str] = {}
    copies: dict[Path, tuple[bytes, int]] = {}
    for path, content in outputs.items():
        if isinstance(content, FrozenFile):
            copies[path] = (content.content, content.mode)
        elif isinstance(content, Copied):
            copies[path] = (content.read_bytes(), content.file_mode())
        elif isinstance(content, Merged):
            if (applied := created_document(path, content)) is not None:
                texts[path] = applied
            texts.update(content.records)
        else:
            texts[path] = content
    changes = [_text_change(path, wanted) for path, wanted in texts.items()]
    changes += [_copy_change(path, *copy) for path, copy in copies.items()]
    return changes


def _text_change(path: Path, wanted: str) -> RenderedChange:
    if not path.is_file():
        return RenderedChange(path, "create", "", wanted)
    actual = path.read_text(encoding="utf-8", newline="")
    same = normalise(path, actual) == normalise(path, wanted)
    return RenderedChange(path, "identical" if same else "update", actual, wanted)


def _copy_change(path: Path, content: bytes, mode: int) -> RenderedChange:
    wanted = _copy_summary(content, mode)
    if not path.is_file():
        return RenderedChange(path, "create", "", wanted)
    actual = _copy_summary(path.read_bytes(), path.stat().st_mode)
    return RenderedChange(path, "identical" if actual == wanted else "update", actual, wanted)


def _entries_for(outputs: Mapping[Path, Output]) -> dict[Path, WrittenEntry]:
    entries: dict[Path, WrittenEntry] = {}
    for path, content in outputs.items():
        if isinstance(content, FrozenFile):
            entries[path] = frozen_entry(content.content, content.mode)
        elif isinstance(content, Copied):
            entries[path] = copied_entry(content.source, content.prefix, mode=content.file_mode())
        elif isinstance(content, Merged):
            entries[path] = merged_entry(content.owned)
        else:
            entries[path] = text_entry(path, content)
    return entries


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


DRIFT_ADVICE = {
    "source-changed": "the source changed since the last sync; run `loadout sync`",
    "missing": "not on disk; run `loadout sync`",
    "hand-edited": "edited outside loadout; move the edit into the source, or run "
    "`loadout sync --force` to discard it",
    "unknown": "no record of what loadout wrote here and no committed baseline, so a source "
    "change cannot be told from a hand edit; review the diff before syncing",
    "orphaned": "written by loadout but no longer rendered; run `loadout sync` to retire it",
}


def classified_drift(
    root: Path, profile: str
) -> tuple[list[tuple[Path, str, str]], dict[Path, str]]:
    """Every drifted path, retirements included, with its kind from the guard's verdict.

    A merged output is outside the guard, which records owned keys but no hash, so
    its drift is unknown rather than a guess. An orphan the guard accepts is
    orphaned; one it refuses is hand-edited, because sync will refuse it.
    """
    drift = check_all(root, profile)
    outputs = render_all(root, profile)
    orphans = orphaned(read_written(root), outputs)
    drift += orphan_drift(orphans)
    verdicts = {u.path: u.kind for u in unaccepted_outputs(root, outputs, profile)}

    def kind(path: Path) -> str:
        if not path.exists():
            return "missing"
        if path in orphans:
            return verdicts.get(path, "orphaned")
        if isinstance(outputs.get(path), Merged):
            return "unknown"
        return verdicts.get(path, "source-changed")

    return drift, {path: kind(path) for path, _, _ in drift}


def _check_json(root: Path, profile: str) -> int:
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        drift, kinds = classified_drift(root, profile)
    notes = [line.removeprefix("note: ") for line in captured.getvalue().splitlines()]
    notes += _check_notes(root, profile)
    code = 1 if drift else 0
    result = {
        "status": "drift" if drift else "clean",
        "exit_code": code,
        "drift": [{"path": str(path), "kind": kinds[path]} for path, _, _ in drift],
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
    drift, kinds = classified_drift(root, profile)
    for note in _check_notes(root, profile):
        print(f"note: {note}")
    if not drift:
        print("generated files are up to date")
        return 0
    for path, actual, expected in drift:
        rel = _display(path, root)
        print(f"DRIFT ({kinds[path]}): {rel}", file=sys.stderr)
        lines = list(diff_lines(rel, actual, expected, context=3))
        sys.stderr.writelines(lines[:DIFF_LIMIT])
        if len(lines) > DIFF_LIMIT:
            print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=sys.stderr)
    print(f"\n{len(drift)} generated file(s) out of sync:", file=sys.stderr)
    for kind, advice in DRIFT_ADVICE.items():
        count = sum(1 for value in kinds.values() if value == kind)
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
            print(f"{name}: declared, from source {found.source}")
        print(f"    {_display(found.path, root)}")
    return 0


def cmd_template_add(root: Path, name: str | None, mode: str | None) -> int:
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
        mode = _choose_mode(root, name)

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
    return _make_vendored(root, name)


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


def _choose_mode(root: Path, name: str) -> str:
    try:
        choice = input(f"Use {name} as tracked or vendored? [tracked/vendored] ").strip().lower()
    except (EOFError, OSError):
        choice = ""
    if choice not in {"tracked", "vendored"}:
        raise LoadoutError(f"choose tracked or vendored for {name}, not {choice!r}")
    return choice


def _make_vendored(root: Path, name: str) -> int:
    found = resolve_template(name, root)
    local = vendored_path(root, name)
    if found.path.is_file():
        return vendor_catalog(root, name, found)
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


def _report_diff(lines: list[str]) -> None:
    sys.stderr.writelines(lines[:DIFF_LIMIT])
    if len(lines) > DIFF_LIMIT:
        print(f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)", file=sys.stderr)


def cmd_template_sync(root: Path, name: str) -> int:
    """Update a vendored copy from its source, refusing rather than merging.

    Refuse-and-diff is never wrong and never silently mangles anything. The
    alternative — a three-way merge against the version you vendored — is the
    largest single piece of work in this design, and the recorded hash is exactly
    the base it would need, so deferring it costs no state migration.
    """
    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    if local.is_file():
        return vendor_catalog(root, name, resolve_upstream_template(name), update=True)
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
        _report_diff(_tree_diff(local, upstream, name))
        print(
            f"\nSync refused — with no recorded hash, nothing can tell your edits from "
            f"the source's, so the '-' lines above may be either. Commit your copy first "
            f"if you want to keep them, or delete {_display(local, root)} and run "
            f"`loadout template add {name} --vendored` to take the source wholesale,",
            file=sys.stderr,
        )
        return 1

    if recorded is not None and current != recorded:
        print(
            f"WARNING: the vendored copy of {name} was modified after it was vendored",
            file=sys.stderr,
        )
        _report_diff(_tree_diff(local, upstream, name))
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
    _report_diff(changed)
    return 0
