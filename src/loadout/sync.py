"""What `sync` decides and does: the guard's verdict on each output, writing them,
and retiring what is no longer rendered."""

from __future__ import annotations

import functools
import io
import subprocess
import tarfile
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from .emit import Copied, Merged, Output, declared_profiles, render_all
from .errors import LoadoutError
from .files import check_all, created_document, describe_file, write_outputs
from .gitignore import append_lines, gitignore_path, remove_lines
from .output import FrozenFile, sha256
from .project import PROJECT_DIR, gitignore_entries, project_config_path, refuse_escaping
from .retirement import orphan_drift, orphaned, retire
from .unmanaged import describe_unmanaged, unmanaged_entries
from .written import (
    WrittenEntry,
    accepts_bytes,
    accepts_text,
    copied_entry,
    frozen_entry,
    merged_entry,
    normalise,
    read_written,
    record_written,
    text_entry,
)


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
        return _render_variants(Path(tmp_dir), profiles, rebase_to=root)


@dataclass(frozen=True)
class Unaccepted:
    """A file on disk no baseline accounts for. `kind` is hand-edited when a commit or
    a record says what loadout wrote there, and unknown when neither exists. `orphaned`
    marks a recorded destination this run no longer renders, which sync would retire."""

    path: Path
    kind: Literal["hand-edited", "unknown"]
    actual: str
    expected: str
    orphaned: bool = False


def _text_accepted(path: Path, actual: str, forms: set[str], entry: WrittenEntry | None) -> bool:
    return normalise(path, actual) in forms or accepts_text(entry, path, actual)


def _bytes_accepted(data: bytes, expected: bytes | None, entry: WrittenEntry | None) -> bool:
    return data == expected or accepts_bytes(entry, data)


def unaccepted_outputs(root: Path, outputs: Mapping[Path, Output]) -> list[Unaccepted]:
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
    written = read_written(root)

    # The committed source and every other profile cost a render each, so they are
    # built only once some file matches neither this render nor the record.
    @functools.cache
    def committed() -> dict[Path, set[str]]:
        return _committed_variants(root, declared_profiles(root))

    @functools.cache
    def acceptable() -> dict[Path, set[str]]:
        forms = {path: set(found) for path, found in committed().items()}
        for path, found in _render_variants(root, declared_profiles(root), rebase_to=root).items():
            forms.setdefault(path, set()).update(found)
        return forms

    def unaccepted(path: Path, actual: str, expected: str) -> Unaccepted:
        baseline = path in committed() or written.get(path) is not None
        return Unaccepted(path, "hand-edited" if baseline else "unknown", actual, expected)

    modified: list[Unaccepted] = []
    for path, expected in outputs.items():
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
        if not path.is_file():
            continue
        actual = path.read_text(encoding="utf-8")
        if _text_accepted(path, actual, {normalise(path, expected)}, written.get(path)):
            continue
        forms = acceptable().get(path, set())
        if forms and not _text_accepted(path, actual, forms, written.get(path)):
            modified.append(unaccepted(path, actual, expected))
    orphans = orphaned(written, outputs)
    if orphans:
        modified += _unaccepted_orphans(orphans, acceptable())
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
        gitignore_path(root)
        records = (r for c in outputs.values() if isinstance(c, Merged) for r, _ in c.records)
        orphans = orphaned(read_written(root), outputs)
        refuse_escaping(root, (*outputs, *records, *orphans))
    written = write_outputs(outputs)
    record_written(root, _entries_for(outputs))
    if not project_config_path(root).is_file():
        return written, 0
    # Root-anchored, the form init's migration writes: a bare "CLAUDE.md"
    # would also ignore loadout/instructions/native/claude/CLAUDE.md, and
    # `git add loadout` then silently drops that source from the index.
    entries = list(gitignore_entries(root, outputs))
    return written, append_lines(root, entries)


@dataclass(frozen=True)
class RenderedChange:
    """One output against the file on disk. `kind` is identical, update or create;
    `actual` and `wanted` are text for a diff, or a size and digest for a copy.
    `sha256` is the digest of the bytes the output writes, whichever it is."""

    path: Path
    kind: Literal["identical", "update", "create"]
    actual: str
    wanted: str
    sha256: str = ""


def _copy_summary(content: bytes, mode: int) -> str:
    return f"{len(content)} bytes, mode {mode & 0o777:o}, sha256 {sha256(content)}\n"


def compare_with_disk(outputs: Mapping[Path, Output]) -> list[RenderedChange]:
    """What writing `outputs` would do, judged the way the guard judges a change:
    equal under `normalise` is identical, so a banner or a JSON re-serialisation is
    not a difference."""
    texts: dict[Path, str] = {}
    copies: dict[Path, tuple[bytes, int]] = {}
    for path, content in outputs.items():
        match content:
            case FrozenFile():
                copies[path] = (content.content, content.mode)
            case Copied():
                copies[path] = (content.read_bytes(), content.file_mode())
            case Merged():
                if (applied := created_document(path, content)) is not None:
                    texts[path] = applied
                texts.update(content.records)
            case str():
                texts[path] = content
            case _:
                assert_never(content)
    changes = [_text_change(path, wanted) for path, wanted in texts.items()]
    changes += [_copy_change(path, *copy) for path, copy in copies.items()]
    return changes


def _text_change(path: Path, wanted: str) -> RenderedChange:
    digest = sha256(wanted.encode())
    if not path.is_file():
        return RenderedChange(path, "create", "", wanted, digest)
    actual = path.read_text(encoding="utf-8", newline="")
    same = normalise(path, actual) == normalise(path, wanted)
    return RenderedChange(path, "identical" if same else "update", actual, wanted, digest)


def _copy_change(path: Path, content: bytes, mode: int) -> RenderedChange:
    wanted = _copy_summary(content, mode)
    digest = sha256(content)
    if not path.is_file():
        return RenderedChange(path, "create", "", wanted, digest)
    actual = _copy_summary(path.read_bytes(), path.stat().st_mode)
    return RenderedChange(
        path, "identical" if actual == wanted else "update", actual, wanted, digest
    )


def _entries_for(outputs: Mapping[Path, Output]) -> dict[Path, WrittenEntry]:
    entries: dict[Path, WrittenEntry] = {}
    for path, content in outputs.items():
        match content:
            case FrozenFile():
                entries[path] = frozen_entry(content.content)
            case Copied():
                entries[path] = copied_entry(content.source, content.prefix)
            case Merged():
                entries[path] = merged_entry(content.owned)
            case str():
                entries[path] = text_entry(path, content)
            case _:
                assert_never(content)
    return entries


DriftKind = Literal["hand-edited", "unknown", "source-changed", "missing", "orphaned", "unmanaged"]


@dataclass(frozen=True)
class Drift:
    path: Path
    actual: str
    expected: str
    kind: DriftKind


def classified_drift(root: Path, profile: str) -> list[Drift]:
    """Every drifted path, retirements included, with its kind from the guard's verdict.

    A merged output is outside the guard, which records owned keys but no hash, so
    its drift is unknown rather than a guess. An orphan the guard accepts is
    orphaned; one it refuses is hand-edited, because sync will refuse it.
    """
    outputs = render_all(root, profile)
    drift = check_all(root, profile, outputs)
    orphans = orphaned(read_written(root), outputs)
    drift += orphan_drift(orphans)
    verdicts = {u.path: u.kind for u in unaccepted_outputs(root, outputs)}
    found = unmanaged_entries(root, outputs, profile)
    unmanaged = set(found)
    drift += [(path, describe_unmanaged(path), "") for path in found]

    def kind(path: Path) -> DriftKind:
        if path in unmanaged:
            return "unmanaged"
        if not path.exists():
            return "missing"
        if path in orphans:
            return verdicts.get(path, "orphaned")
        if isinstance(outputs.get(path), Merged):
            return "unknown"
        return verdicts.get(path, "source-changed")

    return [Drift(path, actual, expected, kind(path)) for path, actual, expected in drift]
