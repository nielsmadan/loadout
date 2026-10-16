"""What `sync` decides and does: the guard's verdict on each output, writing them,
and retiring what is no longer rendered."""

from __future__ import annotations

import functools
import io
import json
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, assert_never

from .emit import RenderContext, declared_profiles, render_all
from .errors import LoadoutError
from .files import (
    Drift,
    DriftKind,
    check_all,
    copy_matches,
    created_document,
    describe_copy,
    describe_file,
    write_outputs,
)
from .gitignore import append_lines, remove_lines, require_plain_gitignore
from .output import (
    Copied,
    FrozenFile,
    Merged,
    Output,
    file_sha256,
    output_record_paths,
    sha256,
)
from .project import PROJECT_DIR, gitignore_entries, project_config_path, refuse_escaping
from .retirement import orphan_drift, orphaned, retire
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
    read_written,
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
    root: Path, profiles: Iterable[str], context: RenderContext
) -> dict[Path, set[str]]:
    # A project's render reads only its own source folder, so only that is archived.
    scope = ["--", PROJECT_DIR] if project_config_path(root).is_file() else []
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "archive", "HEAD", *scope], capture_output=True, check=False
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
        return _render_variants(Path(tmp_dir), profiles, root, context)


@dataclass(frozen=True, kw_only=True)
class Unaccepted:
    """A file on disk no baseline accounts for. `kind` is hand-edited when a commit or
    a record says what loadout wrote there, and unknown when neither exists. `orphaned`
    marks a recorded destination this run no longer renders, which sync would retire."""

    path: Path
    kind: Literal["hand-edited", "owned-key", "owned-key-removed", "unknown"]
    actual: str
    expected: str
    orphaned: bool = False


def _text_accepted(path: Path, actual: str, forms: set[str], entry: WrittenEntry | None) -> bool:
    return normalise(path, actual) in forms or accepts_text(entry, path, actual)


def _bytes_accepted(data: bytes, expected: bytes | None, entry: WrittenEntry | None) -> bool:
    return data == expected or accepts_bytes(entry, data)


def _guarded(merged: Merged) -> frozenset[str]:
    return merged.owned - merged.evicted


def _owned_forms(merged: Merged) -> set[str]:
    hashes = owned_hashes(merged.document, _guarded(merged), merged.format) or {}
    return {f"{key}\0{digest}" for key, digest in hashes.items()}


def _shown(text: str, keys: Iterable[str], format_name: str) -> str:
    values = owned_values(text, keys, format_name) or {}
    return json.dumps(values, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def _unaccepted_owned(
    path: Path,
    expected: Merged,
    entry: WrittenEntry | None,
    acceptable: Callable[[], Mapping[Path, set[str]]],
) -> Unaccepted | None:
    """The owned keys on disk that neither this render, the record nor a variant
    accounts for, each judged alone. Only a key the record covers has a baseline: one it
    does not is newly managed, a source change, and an evicted key is owned only to be
    stripped. An entry from before per-key hashes covers nothing, as before."""
    keys = _guarded(expected)
    text = path.read_text(encoding="utf-8")
    disk = owned_hashes(text, keys, expected.format)
    want = owned_hashes(expected.document, keys, expected.format) or {}
    if disk is None:
        shown = f"{path.name} does not parse\n"
        return Unaccepted(
            path=path,
            kind="owned-key",
            actual=shown,
            expected=_shown(expected.document, keys, expected.format),
        )
    recorded = dict(entry.owned_sha256) if entry is not None else {}
    unexplained = [
        key
        for key in sorted(keys)
        if disk[key] != want[key]
        and key in recorded
        and disk[key] != recorded[key]
        and f"{key}\0{disk[key]}" not in acceptable().get(path, set())
    ]
    if not unexplained:
        return None
    removed = all(disk[key] == ABSENT for key in unexplained)
    return Unaccepted(
        path=path,
        kind="owned-key-removed" if removed else "owned-key",
        actual=_shown(text, unexplained, expected.format),
        expected=_shown(expected.document, unexplained, expected.format),
    )


def newly_managed(root: Path, outputs: Mapping[Path, Output]) -> list[tuple[Path, str, str]]:
    """Each key a source now manages that the record never covered and the file holds
    with another value: sync overwrites it as a source change, and says so."""
    written = read_written(root)
    found: list[tuple[Path, str, str]] = []
    for path, expected in outputs.items():
        if not isinstance(expected, Merged) or not path.is_file():
            continue
        entry = written.get(path)
        if entry is not None and not entry.owned_sha256:
            continue  # recorded before per-key hashes: no baseline, as before
        recorded = dict(entry.owned_sha256) if entry is not None else {}
        keys = sorted(_guarded(expected) - recorded.keys())
        values = owned_values(path.read_text(encoding="utf-8"), keys, expected.format) or {}
        wanted = owned_values(expected.document, keys, expected.format) or {}
        found += [
            (path, key, _brief(values[key]))
            for key in keys
            if key in values
            and (key not in wanted or canonical(values[key]) != canonical(wanted[key]))
        ]
    return found


BRIEF_LIMIT = 60


def _brief(value: object) -> str:
    text = canonical(value)
    if isinstance(value, dict | list) or len(text) > BRIEF_LIMIT:
        return "another value"
    return text


def refuse_unsafe_orphans(
    root: Path, orphans: Iterable[Path], outputs: Mapping[Path, Output]
) -> None:
    """Before any orphan is read: the repository may have replaced a path sync wrote
    with a link, and reading it would print whatever the link names."""
    if project_config_path(root).is_file():
        refuse_escaping(root, _in_project(root, orphans), outputs)


def _in_project(root: Path, paths: Iterable[Path]) -> list[Path]:
    """The paths the project write rule covers. A root that is also a global source
    renders destinations under ~ too, which are the global scope's and never inside."""
    boundary = root.resolve()
    return [path for path in paths if path.is_relative_to(root) or path.is_relative_to(boundary)]


def unaccepted_outputs(
    root: Path, outputs: Mapping[Path, Output], context: RenderContext | None = None
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
    context = context or RenderContext()
    written = read_written(root)
    refuse_unsafe_orphans(root, orphaned(written, outputs), outputs)

    # The committed source and every other profile cost a render each, so they are
    # built only once some file matches neither this render nor the record.
    @functools.cache
    def committed() -> dict[Path, set[str]]:
        return _committed_variants(root, declared_profiles(root), context)

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

    modified = [
        found
        for path, expected in outputs.items()
        if path.is_file()
        and (found := _judged(path, expected, written.get(path), acceptable, unaccepted))
        is not None
    ]
    orphans = orphaned(written, outputs)
    if orphans:
        modified += _unaccepted_orphans(orphans, acceptable)
    return modified


def _judged(
    path: Path,
    expected: Output,
    entry: WrittenEntry | None,
    acceptable: Callable[[], Mapping[Path, set[str]]],
    unaccepted: Callable[[Path, str, str], Unaccepted],
) -> Unaccepted | None:
    """The guard's verdict on one output already on disk: None when something loadout
    could have written accounts for it."""
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
            digest = file_sha256(path)
            accepted = digest == file_sha256(expected.source) or accepts_digest(entry, digest)
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
    actual = path.read_text(encoding="utf-8")
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
        # A merged orphan loses only loadout's keys, which the guard never protects.
        if entry.kind == "merged" or not path.is_file():
            continue
        if entry.kind == "text":
            actual = path.read_text(encoding="utf-8")
            if not accepts_text(entry, path, actual) and not _text_accepted(
                path, actual, acceptable().get(path, set()), entry
            ):
                modified.append(
                    Unaccepted(
                        path=path, kind="hand-edited", actual=actual, expected="", orphaned=True
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


def retire_orphans(root: Path, outputs: Mapping[Path, Output]) -> tuple[list[Path], int]:
    """Retire every recorded destination this run no longer renders, once the guard
    has passed or been forced, and forget them. Returns the paths changed on disk and
    how many `.gitignore` entries were dropped."""
    written = read_written(root)
    orphans = orphaned(written, outputs)
    if not orphans:
        return [], 0
    retired = retire(orphans, written)
    record_written(root, {}, dropped=orphans)
    if not project_config_path(root).is_file():
        return retired, 0
    # Gone, whether retiring deleted it just now or it was deleted by hand before.
    deleted = [
        path for path, entry in orphans.items() if entry.kind != "merged" and not path.exists()
    ]
    unwanted = set(gitignore_entries(root, deleted)) - set(gitignore_entries(root, outputs))
    return retired, remove_lines(root, unwanted)


def deploy(root: Path, outputs: Mapping[Path, Output]) -> tuple[list[Path], int]:
    """Write rendered outputs, record them, and ignore them: what sync does once the
    guard has passed, shared with init's first render. Returns the paths written and
    how many `.gitignore` entries were added."""
    if project_config_path(root).is_file():
        require_plain_gitignore(root)
        orphans = orphaned(read_written(root), outputs)
        refuse_escaping(
            root, _in_project(root, (*outputs, *output_record_paths(outputs), *orphans)), outputs
        )
    written = write_outputs(outputs)
    record_written(root, _entries_for(outputs))
    if not project_config_path(root).is_file():
        return written, 0
    # Root-anchored, the form init's migration writes: a bare "CLAUDE.md"
    # would also ignore loadout/instructions/native/claude/CLAUDE.md, and
    # `git add loadout` then silently drops that source from the index.
    entries = list(gitignore_entries(root, outputs))
    return written, append_lines(root, entries)


@dataclass(frozen=True, kw_only=True)
class RenderedChange:
    """One output against the file on disk. `kind` is identical, update or create;
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
                if (applied := created_document(path, content)) is not None:
                    texts[path] = applied
                texts.update(content.records)
            case str():
                texts[path] = content
            case _:
                assert_never(content)
    changes = [_text_change(path, wanted) for path, wanted in texts.items()]
    changes += [_copy_change(path, copy) for path, copy in copies.items()]
    return changes


def _text_change(path: Path, wanted: str) -> RenderedChange:
    digest = sha256(wanted.encode())
    if not path.is_file():
        return RenderedChange(path=path, kind="create", actual="", expected=wanted, sha256=digest)
    actual = path.read_text(encoding="utf-8", newline="")
    kind: Literal["identical", "normalised", "update"] = (
        "identical"
        if actual == wanted
        # Only loadout's banner or a re-serialisation differs: nothing of the user's changes.
        else "normalised"
        if normalise(path, actual) == normalise(path, wanted)
        else "update"
    )
    return RenderedChange(path=path, kind=kind, actual=actual, expected=wanted, sha256=digest)


def _copy_change(path: Path, copy: FrozenFile | Copied) -> RenderedChange:
    wanted = describe_copy(copy)
    digest = sha256(copy.content) if isinstance(copy, FrozenFile) else file_sha256(copy.source)
    if not path.is_file():
        return RenderedChange(path=path, kind="create", actual="", expected=wanted, sha256=digest)
    kind: Literal["identical", "update"] = "identical" if copy_matches(path, copy) else "update"
    return RenderedChange(
        path=path, kind=kind, actual=describe_file(path), expected=wanted, sha256=digest
    )


def _entries_for(outputs: Mapping[Path, Output]) -> dict[Path, WrittenEntry]:
    entries: dict[Path, WrittenEntry] = {}
    for path, content in outputs.items():
        match content:
            case FrozenFile():
                entries[path] = frozen_entry(content.content)
            case Copied():
                entries[path] = copied_entry(content.source)
            case Merged():
                entries[path] = merged_entry(content.owned, content.document, content.format)
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
    orphans = orphaned(read_written(root), outputs)
    refuse_unsafe_orphans(root, orphans, outputs)
    drift = check_all(outputs)
    drift += orphan_drift(orphans)
    verdicts = {u.path: u.kind for u in unaccepted_outputs(root, outputs, context)}
    found = unmanaged_entries(root, outputs, profile)
    unmanaged = set(found)
    drift += [
        Drift(path=path, actual=describe_unmanaged(path), expected="", kind="unmanaged")
        for path in found
    ]

    def kind(path: Path) -> DriftKind:
        if path in unmanaged:
            return "unmanaged-link" if path.is_symlink() else "unmanaged"
        if not path.exists():
            return "missing"
        if path in orphans:
            return verdicts.get(path, "orphaned")
        return verdicts.get(path, "source-changed")

    return [replace(item, kind=kind(item.path)) for item in drift]
