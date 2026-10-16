"""Writing rendered outputs to disk and comparing them with what is there."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, assert_never

from .atomic import replace_atomically
from .credentials import credential_material
from .errors import LoadoutError
from .output import Copied, FrozenFile, Merged, Output, file_sha256, sha256
from .surgery import DocumentFormat, apply
from .written import owned_values

# What the guard refuses an output for, so sync and check name a refusal alike.
GuardKind = Literal["hand-edited", "owned-key", "owned-key-removed", "unknown"]
DriftKind = Literal[
    GuardKind,
    "source-changed",
    "missing",
    "orphaned",
    "unmanaged",
    "unmanaged-link",
    "unparsable",
]


@dataclass(frozen=True, kw_only=True)
class Drift:
    path: Path
    actual: str
    expected: str
    kind: DriftKind


def _target(path: Path) -> Path:
    # A destination is often a symlink into the user's config repo (the pre-loadout
    # deployment mechanism). os.replace() on a symlink replaces the link itself, not
    # its target — write through the link instead, so the symlink survives sync.
    return path.resolve() if path.is_symlink() else path


def _write_through(path: Path, fill: Callable[[BinaryIO], object], mode: int) -> None:
    replace_atomically(_target(path), fill, mode=mode)


SECRET_MODE = 0o600


def text_mode(existing: int | None, content: bytes, format_name: str, umask: int) -> int:
    """A text output's mode (review 4, batch 1.4): a file replaced keeps the mode it has,
    so a chmod survives; a new one gets what the umask allows, or owner-only when it
    holds a secret the credential check recognises."""
    if existing is not None:
        return existing
    if credential_material(content, format_name) is not None:
        return SECRET_MODE
    return 0o666 & ~umask


def _existing_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return None


def process_umask() -> int:
    """The umask, which can only be read by setting it, so it is set straight back."""
    mask = os.umask(0o077)
    os.umask(mask)
    return mask


def atomic_write(path: Path, content: str, umask: int | None = None) -> None:
    """`umask` is one a caller writing many files read once: reading it sets it, and
    did so for every file (review 5, batch 3.3)."""
    target = _target(path)
    data = content.encode("utf-8")
    mask = process_umask() if umask is None else umask
    mode = text_mode(_existing_mode(target), data, target.suffix[1:], mask)
    replace_atomically(target, lambda handle: handle.write(data), mode=mode)


def write_outputs(
    outputs: Mapping[Path, Output], sources: Callable[[Path], str] | None = None
) -> list[Path]:
    """`sources` is a digest of copied sources a caller already reads them through."""
    frozen: dict[Path, str | FrozenFile | Copied] = {}
    for path, content in outputs.items():
        frozen.update(_frozen(path, content))
    written: list[Path] = []
    umask = process_umask()
    for path, frozen_content in frozen.items():
        if _unchanged(path, frozen_content, sources):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        match frozen_content:
            case FrozenFile():
                _atomic_frozen_copy(path, frozen_content.content, frozen_content.mode)
            case Copied():
                _atomic_stream_copy(path, frozen_content)
            case str():
                atomic_write(path, frozen_content, umask)
            case _:
                assert_never(frozen_content)
        written.append(path)
    return written


def _frozen(path: Path, content: Output) -> dict[Path, str | FrozenFile | Copied]:
    match content:
        case FrozenFile() | Copied() | str():
            # A copy stays a reference and is streamed when written, never held whole.
            return {path: content}
        case Merged():
            # Reached with an unparsable document only under --force; sync refuses first.
            return dict(merged_texts(path, content, replace_unparsable=True))
        case _:
            assert_never(content)


def _unchanged(
    path: Path, content: str | FrozenFile | Copied, sources: Callable[[Path], str] | None
) -> bool:
    """Same bytes already there, so rewriting would only move the mtime."""
    match content:
        case Copied() | FrozenFile():
            return copy_matches(path, content, sources)
        case str():
            return path.is_file() and path.read_bytes() == content.encode("utf-8")
        case _:
            assert_never(content)


def check_all(outputs: Mapping[Path, Output]) -> list[Drift]:
    """Every output on disk that differs from `outputs`, byte for byte, as a source
    change until the guard says otherwise."""
    drift: list[Drift] = []

    def differs(path: Path, actual: str, expected: str) -> None:
        drift.append(Drift(path=path, actual=actual, expected=expected, kind="source-changed"))

    def text_differs(path: Path, expected: str, newline: str | None = "") -> None:
        actual = read_document(path, newline) if path.is_file() else ""
        if actual is None:
            differs(path, shown_document(path), expected)
        elif actual != expected:
            differs(path, actual, expected)

    for path, expected in outputs.items():
        match expected:
            case FrozenFile() | Copied():
                if not copy_matches(path, expected):
                    differs(path, describe_file(path), describe_copy(expected))
            case Merged():
                if unparsable(path, expected):
                    actual = shown_document(path)
                    replacement = applied_document(path, expected, replace_unparsable=True)
                    drift.append(
                        Drift(path=path, actual=actual, expected=replacement, kind="unparsable")
                    )
                    continue
                # Applying is identity on everything loadout does not own, so this
                # compares owned keys alone: the harness writing its own project table
                # cannot read as drift.
                for written, text in merged_texts(path, expected).items():
                    text_differs(written, text, newline="" if written == path else None)
            case str():
                text_differs(path, expected)
            case _:
                assert_never(expected)
    return drift


CHUNK = 1 << 20


def copy_matches(
    path: Path, copy: Copied | FrozenFile, sources: Callable[[Path], str] | None = None
) -> bool:
    """Whether `path` already holds `copy`: its bytes and every mode bit. The one rule
    check, the preview and the writer share, so none of them disagrees about a copy.
    With `sources`, a copy's source is compared by the digest it already has, so a sync
    reads each source once (review 4, batch 3.4)."""
    if not path.is_file():
        return False
    if stat.S_IMODE(path.stat().st_mode) != _mode(copy):
        return False
    if isinstance(copy, FrozenFile):
        return path.read_bytes() == copy.content
    if sources is None:
        return _same_bytes(path, copy.source)
    return path.stat().st_size == copy.source.stat().st_size and file_sha256(path) == sources(
        copy.source
    )


def _mode(copy: Copied | FrozenFile) -> int:
    return stat.S_IMODE(copy.mode if isinstance(copy, FrozenFile) else copy.file_mode())


def describe_copy(copy: Copied | FrozenFile) -> str:
    if isinstance(copy, FrozenFile):
        return _summary(len(copy.content), _mode(copy), sha256(copy.content))
    return _summary(copy.source.stat().st_size, _mode(copy), file_sha256(copy.source))


def describe_file(path: Path) -> str:
    # Drift on a copied file reports a summary, not the content: a tree carries
    # binaries, and a byte diff of one is noise rather than a review.
    if not path.is_file():
        return "(absent)\n"
    metadata = path.stat()
    return _summary(metadata.st_size, metadata.st_mode, file_sha256(path))


def _summary(size: int, mode: int, digest: str) -> str:
    return f"{size} bytes, mode {stat.S_IMODE(mode):o}, sha256 {digest}\n"


def _same_bytes(path: Path, source: Path) -> bool:
    """Compared a chunk at a time, so a large copy is never held whole."""
    if path.stat().st_size != source.stat().st_size:
        return False
    with path.open("rb") as existing, source.open("rb") as source_file:
        while True:
            chunk = source_file.read(CHUNK)
            if existing.read(CHUNK) != chunk:
                return False
            if not chunk:
                return True


def _atomic_stream_copy(path: Path, copied: Copied) -> None:
    def fill(handle: BinaryIO) -> None:
        with copied.source.open("rb") as source:
            shutil.copyfileobj(source, handle, CHUNK)

    _write_through(path, fill, copied.file_mode())


def _atomic_frozen_copy(path: Path, content: bytes, mode: int) -> None:
    _write_through(path, lambda handle: handle.write(content), mode)


class UnparsableDocument(LoadoutError):
    """A document loadout shares with a harness that no longer parses. One the source no
    longer renders has nothing to replace it with, so only fixing or deleting it helps."""

    def __init__(self, path: Path, format_name: str, *, orphaned: bool = False) -> None:
        kind = format_name.upper()
        if orphaned:
            super().__init__(
                f"{path} is not a {kind} document loadout can remove its keys from; fix or "
                "delete it, then run `loadout sync`"
            )
            return
        super().__init__(
            f"{path} is not a {kind} document loadout can write its keys into; fix or delete "
            "it, then run `loadout sync`, or run `loadout sync --force` to replace it"
        )


def document_format(path: Path) -> DocumentFormat:
    """The format of a shared document loadout recorded writing, by its name."""
    return "json" if path.suffix == ".json" else "toml"


def read_document(path: Path, newline: str | None = "") -> str | None:
    """The one reader of a document loadout shares with a harness: its text, or None when
    it is not UTF-8, which no parser reads and every caller takes as unparsable (review 4,
    batch 1.5)."""
    try:
        return path.read_text(encoding="utf-8", newline=newline)
    except UnicodeDecodeError:
        return None


def shown_document(path: Path) -> str:
    """A shared document as a diff shows it, whatever bytes it holds."""
    return path.read_bytes().decode("utf-8", errors="replace")


def unparsable(path: Path, merged: Merged) -> bool:
    """Whether the shared document on disk can no longer hold loadout's keys."""
    return unparsable_as(path, merged.format, merged.owned)


def unparsable_as(path: Path, format_name: str, owned: Iterable[str]) -> bool:
    if not path.is_file():
        return False
    text = read_document(path)
    return text is None or owned_values(text, owned, format_name) is None


def refuse_unparsable(outputs: Mapping[Path, Output]) -> None:
    for path, output in outputs.items():
        if isinstance(output, Merged) and unparsable(path, output):
            raise UnparsableDocument(path, output.format)


def merged_texts(
    path: Path, merged: Merged, *, replace_unparsable: bool = False
) -> dict[Path, str]:
    """Every text a merged output writes: the shared document with loadout's keys in it,
    unless it is absent and would stay empty, and the records beside it."""
    applied = created_document(path, merged, replace_unparsable=replace_unparsable)
    return {**({path: applied} if applied is not None else {}), **dict(merged.records)}


def created_document(path: Path, merged: Merged, *, replace_unparsable: bool = False) -> str | None:
    """The applied document, or None when the file is absent and would stay empty."""
    applied = applied_document(path, merged, replace_unparsable=replace_unparsable)
    return applied if applied or path.is_file() else None


def applied_document(path: Path, merged: Merged, *, replace_unparsable: bool = False) -> str:
    """The destination with loadout's keys written in. The caller reads the file;
    the renderer that produced `merged` never did (ADR 0001). A document that does not
    parse has nothing to preserve, so it is replaced only when asked to be."""
    existing = read_document(path) if path.is_file() else ""
    # The text just read, not a second read of the file (review 5, batch 3.3).
    if existing is None or owned_values(existing, merged.owned, merged.format) is None:
        if not replace_unparsable:
            raise UnparsableDocument(path, merged.format)
        existing = ""
    return apply(existing, merged.owned, merged.document, format_name=merged.format)
