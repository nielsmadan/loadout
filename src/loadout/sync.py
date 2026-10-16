"""What `sync` decides and does: the guard's verdict on each output, writing them,
and retiring what is no longer rendered."""

from __future__ import annotations

import functools
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, assert_never

from . import git_runner
from .containment import refuse_escaping, within
from .credentials import findings, names_a_secret
from .emit import RenderContext, declared_profiles, render_all
from .errors import LoadoutError
from .files import (
    Drift,
    DriftKind,
    GuardKind,
    UnparsableDocument,
    applied_document,
    check_all,
    copy_matches,
    describe_copy,
    describe_file,
    document_format,
    merged_texts,
    read_document,
    shown_document,
    unparsable_as,
    write_outputs,
)
from .git_tree import render_inputs, tree_entries, write_snapshot
from .gitignore import append_lines, remove_lines, require_plain_gitignore
from .machine import is_registered, registered_at
from .output import (
    Copied,
    FrozenFile,
    Merged,
    Output,
    digests,
    file_sha256,
    output_record_paths,
    sha256,
)
from .project import gitignore_entries
from .retirement import orphan_drift, orphaned, retire, stripped
from .state import SyncState, sync_state
from .unmanaged import describe_unmanaged, unmanaged_entries
from .written import (
    ABSENT,
    WrittenEntry,
    accepts_bytes,
    accepts_digest,
    accepts_text,
    canonical,
    copied_entry,
    frozen_entry,
    merged_entry,
    normalise,
    owned_hashes,
    owned_values,
    record_written,
    text_entry,
)


def _render_variants(
    source_root: Path, profiles: Iterable[str], rebase_to: Path, context: RenderContext
) -> dict[Path, set[str]]:
    variants: dict[Path, set[str]] = {}
    for profile in profiles:
        try:
            rendered = render_all(source_root, profile, context)
        except LoadoutError:
            continue
        for path, content in rendered.items():
            match content:
                case FrozenFile() | Copied():
                    continue  # verbatim by definition, so it has no per-profile form to vary
                case Merged():
                    # The rest of the file is the harness's, so only owned keys vary.
                    key = _rebased(path, source_root, rebase_to)
                    variants.setdefault(key, set()).update(_owned_forms(content))
                case str():
                    key = _rebased(path, source_root, rebase_to)
                    variants.setdefault(key, set()).add(normalise(key, content))
                case _:
                    assert_never(content)
    return variants


def _rebased(path: Path, source_root: Path, rebase_to: Path) -> Path:
    try:
        return rebase_to / path.relative_to(source_root)
    except ValueError:
        return path  # a destination outside the root renders to the same path


def _committed_variants(
    state: SyncState, profiles: Iterable[str], context: RenderContext
) -> dict[Path, set[str]]:
    root = state.root
    try:
        repository = git_runner.toplevel(root)
    except LoadoutError:
        return {}
    if repository is None:
        return {}
    relative = root.resolve().relative_to(repository)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tree = Path(tmp_dir) / "head"
        # What the render reads and nothing else, as `check --staged` writes it out: the
        # whole repository cost 20 s on 64k files (review 4, batch 2.4).
        try:
            entries = tree_entries(repository, "HEAD")
            write_snapshot(repository, tree, render_inputs(repository, relative, entries))
        except (LoadoutError, OSError):
            return {}
        # The committed tree of the registered source is that source at HEAD.
        committed = tree / relative
        if not committed.is_dir():
            return {}
        if is_registered(root, context.registration().source):
            context = replace(context, registration=registered_at(committed))
        return _render_variants(committed, profiles, root, context)


@dataclass(frozen=True, kw_only=True)
class Unaccepted:
    """A file on disk no baseline accounts for. `kind` is hand-edited when a commit or
    a record says what loadout wrote there, owned-key or owned-key-removed for one of
    loadout's keys in a document it shares, and unknown when nothing does. `orphaned`
    marks a recorded destination this run no longer renders, which sync would retire."""

    path: Path
    kind: GuardKind
    actual: str
    expected: str
    orphaned: bool = False


def _text_accepted(path: Path, actual: str, forms: set[str], entry: WrittenEntry | None) -> bool:
    return normalise(path, actual) in forms or accepts_text(entry, path, actual)


def _bytes_accepted(data: bytes, expected: bytes | None, entry: WrittenEntry | None) -> bool:
    return data == expected or accepts_bytes(entry, data)


def _owned_forms(merged: Merged) -> set[str]:
    hashes = owned_hashes(merged.document, merged.guarded, merged.format) or {}
    return {f"{key}\0{digest}" for key, digest in hashes.items()}


def _unaccepted_owned(
    path: Path,
    expected: Merged,
    entry: WrittenEntry | None,
    acceptable: Callable[[], Mapping[Path, set[str]]],
) -> Unaccepted | None:
    """The owned keys on disk that neither this render, the record nor a variant
    accounts for, each judged alone. A key a per-key entry does not cover is newly
    managed, a source change, and an evicted key is owned only to be stripped. With no
    per-key entry, none at all or one from before per-key hashes, nothing is newly
    managed: a value on disk no variant explains is refused as `unknown` (review 3, 1.2)."""
    keys = expected.guarded
    found = _disk_hashes(path, keys, expected.format)
    if found is None:
        # Unparsable, its own kind: sync refuses it before the guard runs, and check
        # names it as that whatever the guard says (review 4, batch 4.2).
        return None
    text, disk = found
    want = owned_hashes(expected.document, keys, expected.format) or {}
    recorded = dict(entry.owned_sha256) if entry is not None else {}
    unexplained = _unexplained(keys, disk, want, recorded, lambda: acceptable().get(path, set()))
    if not unexplained:
        return None
    removed = all(disk[key] == ABSENT for key in unexplained)
    # The document as check diffs it, so the abort and check show the same lines.
    return Unaccepted(
        path=path,
        kind="unknown" if not recorded else "owned-key-removed" if removed else "owned-key",
        actual=text,
        expected=applied_document(path, expected),
    )


def _disk_hashes(
    path: Path, keys: Iterable[str], format_name: str
) -> tuple[str, dict[str, str]] | None:
    """A shared document's text and the hash of each of `keys` in it, or None when it
    does not parse, which is its own kind (review 5, batch 3.3)."""
    text = read_document(path, newline=None)
    hashes = None if text is None else owned_hashes(text, keys, format_name)
    return None if text is None or hashes is None else (text, hashes)


def _unexplained(
    keys: Iterable[str],
    disk: Mapping[str, str],
    want: Mapping[str, str],
    recorded: Mapping[str, str],
    forms: Callable[[], set[str]],
) -> list[str]:
    """The owned keys whose value on disk neither what sync writes now (`want`, absent
    when it writes nothing), the record nor a variant accounts for. With a per-key record
    only the keys it covers are judged; with none, every key the file holds."""
    return [
        key
        for key in sorted(keys)
        if disk[key] != want.get(key, ABSENT)
        and (key in recorded if recorded else disk[key] != ABSENT)
        and disk[key] != recorded.get(key)
        and f"{key}\0{disk[key]}" not in forms()
    ]


def newly_managed(state: SyncState, outputs: Mapping[Path, Output]) -> list[tuple[Path, str, str]]:
    """Each key a source now manages that the record never covered and the file holds
    with another value: sync overwrites it as a source change, and says so."""
    written = state.written
    found: list[tuple[Path, str, str]] = []
    for path, expected in outputs.items():
        if not isinstance(expected, Merged) or not path.is_file():
            continue
        entry = written.get(path)
        if entry is None or not entry.owned_sha256:
            continue  # with no per-key entry the guard has judged every key already
        recorded = dict(entry.owned_sha256)
        keys = sorted(expected.guarded - recorded.keys())
        text = read_document(path, newline=None)
        values = (None if text is None else owned_values(text, keys, expected.format)) or {}
        rendered = owned_values(expected.document, keys, expected.format) or {}
        found += [
            (path, key, _brief(key, values[key]))
            for key in keys
            if key in values
            and (key not in rendered or canonical(values[key]) != canonical(rendered[key]))
        ]
    return found


BRIEF_LIMIT = 60


def _brief(key: str, value: object) -> str:
    """The value a newly managed key held, short enough to show, and never one the
    credential check flags by its key or its shape (review 3, 1.7)."""
    text = canonical(value)
    flagged = names_a_secret(key.rsplit(".", 1)[-1]) or bool(findings(text.encode()))
    if isinstance(value, dict | list) or len(text) > BRIEF_LIMIT or flagged:
        return "another value"
    return text


def refuse_unsafe_orphans(
    state: SyncState, orphans: Iterable[Path], outputs: Mapping[Path, Output]
) -> None:
    """Before any orphan is read: the repository may have replaced a path sync wrote
    with a link, and reading it would print whatever the link names."""
    if state.project:
        refuse_escaping(state.root, _in_project(state.root, orphans), outputs)


def _in_project(root: Path, paths: Iterable[Path]) -> list[Path]:
    """The paths the project write rule covers. A root that is also a global source
    renders destinations under ~ too, which are the global scope's and never inside."""
    boundary = root.resolve()
    return [path for path in paths if within(root, path, boundary) is not None]


def unaccepted_outputs(
    state: SyncState,
    outputs: Mapping[Path, Output],
    sources: Callable[[Path], str] | None = None,
    context: RenderContext | None = None,
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
    # The third variant, and the only one that knows about a source state which was
    # never committed and has since moved on. See written.py and ADR 0019.
    root = state.root
    # The command's, so the registration it read decides here too (review 5, batch 3.3).
    context = context or RenderContext()
    written = state.written
    orphans = orphaned(written, outputs)
    refuse_unsafe_orphans(state, orphans, outputs)

    # The committed source and every other profile cost a render each, so they are
    # built only once some file matches neither this render nor the record.
    @functools.cache
    def committed() -> dict[Path, set[str]]:
        return _committed_variants(state, declared_profiles(root), context)

    @functools.cache
    def acceptable() -> dict[Path, set[str]]:
        forms = {path: set(found) for path, found in committed().items()}
        for path, found in _render_variants(root, declared_profiles(root), root, context).items():
            forms.setdefault(path, set()).update(found)
        return forms

    def unaccepted(path: Path, actual: str, expected: str) -> Unaccepted:
        baseline = written.get(path) is not None or path in committed()
        return Unaccepted(
            path=path,
            kind="hand-edited" if baseline else "unknown",
            actual=actual,
            expected=expected,
        )

    # One pass: a source copied to several harnesses is read once, and with `sources`
    # once a sync.
    run = _Pass(acceptable, unaccepted, digests(), sources or digests())
    modified = [
        found
        for path, expected in outputs.items()
        if path.is_file() and (found := _judged(path, expected, written.get(path), run)) is not None
    ]
    if orphans:
        modified += _unaccepted_orphans(orphans, acceptable)
    return modified


@dataclass(frozen=True)
class _Pass:
    """What one run of the guard shares across its outputs: the variants it builds only
    when asked, how it names a refusal, and the file digests it has already read."""

    acceptable: Callable[[], Mapping[Path, set[str]]]
    unaccepted: Callable[[Path, str, str], Unaccepted]
    digest: Callable[[Path], str]
    # Copied sources alone: sync rewrites a destination after the guard has hashed it.
    sources: Callable[[Path], str]


def _judged(
    path: Path, expected: Output, entry: WrittenEntry | None, run: _Pass
) -> Unaccepted | None:
    """The guard's verdict on one output already on disk: None when something loadout
    could have written accounts for it."""
    acceptable, unaccepted, digest = run.acceptable, run.unaccepted, run.digest
    match expected:
        case FrozenFile():
            accepted = _bytes_accepted(path.read_bytes(), expected.content, entry)
            return (
                None if accepted else unaccepted(path, describe_file(path), describe_copy(expected))
            )
        case Copied():
            # A copied file has no per-profile form, so it is compared against its
            # source directly. check_all only *reports* drift; this is the guard
            # that stops sync overwriting a hand edit, and it has to cover a
            # supporting file just as much as a SKILL.md.
            found = digest(path)
            accepted = found == run.sources(expected.source) or accepts_digest(entry, found)
            return (
                None if accepted else unaccepted(path, describe_file(path), describe_copy(expected))
            )
        case Merged():
            # Applying replaces only the owned keys, so only they can carry an edit
            # sync would destroy; the harness's own keys pass through untouched.
            return _unaccepted_owned(path, expected, entry, acceptable)
        case str():
            return _unaccepted_text(path, expected, entry, acceptable, unaccepted)
        case _:
            assert_never(expected)


def _unaccepted_text(
    path: Path,
    expected: str,
    entry: WrittenEntry | None,
    acceptable: Callable[[], Mapping[Path, set[str]]],
    unaccepted: Callable[[Path, str, str], Unaccepted],
) -> Unaccepted | None:
    actual = read_document(path, newline=None)
    if actual is None:
        # Not UTF-8, so nothing loadout writes matches it (review 4, batch 1.5).
        return unaccepted(path, shown_document(path), expected)
    if _text_accepted(path, actual, {normalise(path, expected)}, entry):
        return None
    # The other variants cost a render each, so they are read only now.
    forms = acceptable().get(path, set())
    if forms and not _text_accepted(path, actual, forms, entry):
        return unaccepted(path, actual, expected)
    return None


def _unaccepted_orphans(
    orphans: Mapping[Path, WrittenEntry], acceptable: Callable[[], Mapping[Path, set[str]]]
) -> list[Unaccepted]:
    """Each orphan edited since sync wrote it. The record answers first: the variants
    cost a render each, and an orphan sync left as it was needs none of them."""
    modified: list[Unaccepted] = []
    for path, entry in sorted(orphans.items()):
        if not path.is_file():
            continue
        if entry.kind == "merged":
            if (found := _unaccepted_merged_orphan(path, entry, acceptable)) is not None:
                modified.append(found)
        elif entry.kind == "text":
            actual = read_document(path, newline=None)
            if actual is None or (
                not accepts_text(entry, path, actual)
                and not _text_accepted(path, actual, acceptable().get(path, set()), entry)
            ):
                shown = shown_document(path) if actual is None else actual
                modified.append(
                    Unaccepted(
                        path=path, kind="hand-edited", actual=shown, expected="", orphaned=True
                    )
                )
        elif not accepts_digest(entry, file_sha256(path)):
            modified.append(
                Unaccepted(
                    path=path,
                    kind="hand-edited",
                    actual=describe_file(path),
                    expected="(absent)\n",
                    orphaned=True,
                )
            )
    return modified


def _unaccepted_merged_orphan(
    path: Path, entry: WrittenEntry, acceptable: Callable[[], Mapping[Path, set[str]]]
) -> Unaccepted | None:
    """Retiring strips every key loadout recorded writing, so each one still on disk is
    judged first, against its recorded hash or, with none, the variants (review 3, 1.1).
    A key owned only to be evicted has no hash and is stripped whatever it holds, as a
    live sync strips it (review 4, batch 2.1)."""
    recorded = dict(entry.owned_sha256)
    keys = entry.guarded
    found = _disk_hashes(path, keys, document_format(path))
    if found is None:
        return None
    text, disk = found
    # Retiring wants every key gone.
    unexplained = _unexplained(keys, disk, {}, recorded, lambda: acceptable().get(path, set()))
    if not unexplained:
        return None
    return Unaccepted(
        path=path,
        kind="owned-key" if recorded else "unknown",
        actual=text,
        expected=stripped(path, entry, text),
        orphaned=True,
    )


def refuse_unparsable_orphans(state: SyncState, outputs: Mapping[Path, Output]) -> None:
    """Retiring a shared document strips loadout's keys from it, which one that no longer
    parses cannot have done to it, forced or not."""
    for path, entry in sorted(orphaned(state.written, outputs).items()):
        format_name = document_format(path)
        if entry.kind == "merged" and unparsable_as(path, format_name, entry.owned):
            raise UnparsableDocument(path, format_name, orphaned=True)


def retire_orphans(state: SyncState, outputs: Mapping[Path, Output]) -> tuple[list[Path], int]:
    """Retire every recorded destination this run no longer renders, once the guard
    has passed or been forced, and forget them. Returns the paths changed on disk and
    how many `.gitignore` entries were dropped. The record read before deploying names
    the same orphans: deploying records only what this run renders."""
    root = state.root
    orphans = orphaned(state.written, outputs)
    if not orphans:
        return [], 0
    retired = retire(orphans, state.written)
    record_written(root, {}, dropped=orphans)
    if not state.project:
        return retired, 0
    # Gone, whether retiring deleted it just now or it was deleted by hand before.
    deleted = [
        path for path, entry in orphans.items() if entry.kind != "merged" and not path.exists()
    ]
    unwanted = set(gitignore_entries(root, deleted)) - set(gitignore_entries(root, outputs))
    return retired, remove_lines(root, unwanted)


def deploy(
    state: SyncState, outputs: Mapping[Path, Output], sources: Callable[[Path], str] | None = None
) -> tuple[list[Path], int]:
    """Write rendered outputs, record them, and ignore them: what sync does once the
    guard has passed, shared with init's first render. Returns the paths written and
    how many `.gitignore` entries were added."""
    root = state.root
    if state.project:
        require_plain_gitignore(root)
        orphans = orphaned(state.written, outputs)
        refuse_escaping(
            root, _in_project(root, (*outputs, *output_record_paths(outputs), *orphans)), outputs
        )
    sources = sources or digests()
    written = write_outputs(outputs, sources)
    # A shared document that would stay empty is never created, so it is no record of
    # loadout's to undo (review 3, batch 2.5), and an entry from when it was is dropped:
    # its hashes would judge the next one the harness makes (review 4, batch 2.2).
    recorded = _entries_for(outputs, sources)
    record_written(
        root,
        {path: entry for path, entry in recorded.items() if path.is_file()},
        dropped=[path for path in recorded if not path.is_file()],
    )
    if not state.project:
        return written, 0
    # Root-anchored, the form init's migration writes: a bare "CLAUDE.md"
    # would also ignore loadout/instructions/native/claude/CLAUDE.md, and
    # `git add loadout` then silently drops that source from the index.
    entries = list(gitignore_entries(root, outputs))
    return written, append_lines(root, entries)


@dataclass(frozen=True, kw_only=True)
class RenderedChange:
    """One output against the file on disk. `kind` is identical, normalised (equal once
    loadout's banner and formatting are set aside), update or create;
    `actual` and `expected` are text for a diff, or a size and digest for a copy.
    `sha256` is the digest of the bytes the output writes, whichever it is."""

    path: Path
    kind: Literal["identical", "normalised", "update", "create"]
    actual: str
    expected: str
    sha256: str = ""


def compare_with_disk(outputs: Mapping[Path, Output]) -> list[RenderedChange]:
    """What writing `outputs` would do, judged the way the guard judges a change:
    equal under `normalise` is identical, so a banner or a JSON re-serialisation is
    not a difference."""
    texts: dict[Path, str] = {}
    copies: dict[Path, FrozenFile | Copied] = {}
    for path, content in outputs.items():
        match content:
            case FrozenFile() | Copied():
                copies[path] = content
            case Merged():
                texts.update(merged_texts(path, content))
            case str():
                texts[path] = content
            case _:
                assert_never(content)
    changes = [_text_change(path, expected) for path, expected in texts.items()]
    changes += [_copy_change(path, copy) for path, copy in copies.items()]
    return changes


def _text_change(path: Path, expected: str) -> RenderedChange:
    digest = sha256(expected.encode())
    if not path.is_file():
        return RenderedChange(path=path, kind="create", actual="", expected=expected, sha256=digest)
    actual = read_document(path) or shown_document(path)
    kind: Literal["identical", "normalised", "update"] = (
        "identical"
        if actual == expected
        # Only loadout's banner or a re-serialisation differs: nothing of the user's changes.
        else "normalised"
        if normalise(path, actual) == normalise(path, expected)
        else "update"
    )
    return RenderedChange(path=path, kind=kind, actual=actual, expected=expected, sha256=digest)


def _copy_change(path: Path, copy: FrozenFile | Copied) -> RenderedChange:
    expected = describe_copy(copy)
    digest = sha256(copy.content) if isinstance(copy, FrozenFile) else file_sha256(copy.source)
    if not path.is_file():
        return RenderedChange(path=path, kind="create", actual="", expected=expected, sha256=digest)
    kind: Literal["identical", "update"] = "identical" if copy_matches(path, copy) else "update"
    return RenderedChange(
        path=path, kind=kind, actual=describe_file(path), expected=expected, sha256=digest
    )


def _entries_for(
    outputs: Mapping[Path, Output], sources: Callable[[Path], str] | None = None
) -> dict[Path, WrittenEntry]:
    entries: dict[Path, WrittenEntry] = {}
    digest = sources or digests()
    for path, content in outputs.items():
        match content:
            case FrozenFile():
                entries[path] = frozen_entry(content.content)
            case Copied():
                entries[path] = copied_entry(content.source, digest)
            case Merged():
                entries[path] = merged_entry(content)
            case str():
                entries[path] = text_entry(path, content)
            case _:
                assert_never(content)
    return entries


def classified_drift(root: Path, profile: str, context: RenderContext | None = None) -> list[Drift]:
    """Every drifted path, retirements included, with its kind from the guard's verdict.

    A merged output the guard refuses has an edited owned key. An orphan the guard
    accepts is orphaned; one it refuses is hand-edited, because sync will refuse it.
    """
    outputs = render_all(root, profile, context)
    state = sync_state(root)
    orphans = orphaned(state.written, outputs)
    # First, since it refuses an unsafe orphan before any orphan is read, as the reads
    # below would.
    verdicts = {u.path: u.kind for u in unaccepted_outputs(state, outputs, context=context)}
    drift = check_all(outputs)
    drift += orphan_drift(orphans)
    found = unmanaged_entries(state, outputs, profile)
    unmanaged = set(found)
    drift += [
        Drift(path=path, actual=describe_unmanaged(path), expected="", kind="unmanaged")
        for path in found
    ]

    def kind(item: Drift) -> DriftKind:
        path = item.path
        if item.kind == "unparsable":
            return item.kind
        if path in unmanaged:
            return "unmanaged-link" if path.is_symlink() else "unmanaged"
        if not path.exists():
            return "missing"
        if path in orphans:
            return verdicts.get(path, "orphaned")
        return verdicts.get(path, "source-changed")

    return [replace(item, kind=kind(item)) for item in drift]
