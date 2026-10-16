"""Writing rendered outputs to disk and comparing them with what is there."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal, assert_never

from .output import Copied, FrozenFile, Merged, Output, file_sha256, sha256
from .surgery import apply_json, apply_toml

DriftKind = Literal[
    "hand-edited",
    "owned-key",
    "owned-key-removed",
    "unknown",
    "source-changed",
    "missing",
    "orphaned",
    "unmanaged",
    "unmanaged-link",
]


@dataclass(frozen=True, kw_only=True)
class Drift:
    path: Path
    actual: str
    expected: str
    kind: DriftKind


def _write_through(path: Path, fill: Callable[[BinaryIO], object], mode: int | None) -> None:
    # A destination is often a symlink into the user's config repo (the pre-loadout
    # deployment mechanism). os.replace() on a symlink replaces the link itself, not
    # its target — write through the link instead, so the symlink survives sync.
    target = path.resolve() if path.is_symlink() else path
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".loadout-")
    try:
        with os.fdopen(fd, "wb") as handle:
            fill(handle)
            if mode is not None:
                # Before the rename: an executable must never appear without its bit.
                os.fchmod(handle.fileno(), mode)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def atomic_write(path: Path, content: str) -> None:
    _write_through(path, lambda handle: handle.write(content.encode("utf-8")), None)


def write_outputs(outputs: Mapping[Path, Output]) -> list[Path]:
    frozen: dict[Path, str | FrozenFile | Copied] = {}
    for path, content in outputs.items():
        frozen.update(_frozen(path, content))
    written: list[Path] = []
    for path, frozen_content in frozen.items():
        if _unchanged(path, frozen_content):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        match frozen_content:
            case FrozenFile():
                _atomic_frozen_copy(path, frozen_content.content, frozen_content.mode)
            case Copied():
                _atomic_stream_copy(path, frozen_content)
            case str():
                atomic_write(path, frozen_content)
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
            applied = created_document(path, content)
            return {**({path: applied} if applied is not None else {}), **dict(content.records)}
        case _:
            assert_never(content)


def _unchanged(path: Path, content: str | FrozenFile | Copied) -> bool:
    """Same bytes already there, so rewriting would only move the mtime."""
    match content:
        case Copied() | FrozenFile():
            return copy_matches(path, content)
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

    def text_differs(path: Path, wanted: str, newline: str | None = "") -> None:
        actual = path.read_text(encoding="utf-8", newline=newline) if path.is_file() else ""
        if actual != wanted:
            differs(path, actual, wanted)

    for path, expected in outputs.items():
        match expected:
            case FrozenFile() | Copied():
                if not copy_matches(path, expected):
                    differs(path, describe_file(path), describe_copy(expected))
            case Merged():
                # Applying is identity on everything loadout does not own, so this
                # compares owned keys alone: the harness writing its own project table
                # cannot read as drift.
                text_differs(path, applied_document(path, expected))
                for record_path, text in expected.records:
                    text_differs(record_path, text, newline=None)
            case str():
                text_differs(path, expected)
            case _:
                assert_never(expected)
    return drift


CHUNK = 1 << 20


def copy_matches(path: Path, wanted: Copied | FrozenFile) -> bool:
    """Whether `path` already holds `wanted`: its bytes and every mode bit. The one rule
    check, the preview and the writer share, so none of them disagrees about a copy."""
    if not path.is_file():
        return False
    if stat.S_IMODE(path.stat().st_mode) != _mode(wanted):
        return False
    if isinstance(wanted, FrozenFile):
        return path.read_bytes() == wanted.content
    return _same_bytes(path, wanted.source)


def _mode(wanted: Copied | FrozenFile) -> int:
    return stat.S_IMODE(wanted.mode if isinstance(wanted, FrozenFile) else wanted.file_mode())


def describe_copy(wanted: Copied | FrozenFile) -> str:
    if isinstance(wanted, FrozenFile):
        return _summary(len(wanted.content), _mode(wanted), sha256(wanted.content))
    return _summary(wanted.source.stat().st_size, _mode(wanted), file_sha256(wanted.source))


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


def created_document(path: Path, merged: Merged) -> str | None:
    """The applied document, or None when the file is absent and would stay empty."""
    applied = applied_document(path, merged)
    return applied if applied or path.is_file() else None


def applied_document(path: Path, merged: Merged) -> str:
    """The destination with loadout's keys written in. The caller reads the file;
    the renderer that produced `merged` never did (ADR 0001)."""
    existing = path.read_text(encoding="utf-8", newline="") if path.is_file() else ""
    apply = apply_json if merged.format == "json" else apply_toml
    return apply(existing, merged.owned, merged.document)
