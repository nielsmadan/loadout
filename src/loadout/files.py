"""Writing rendered outputs to disk and comparing them with what is there."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import BinaryIO, assert_never

from .emit import render_all
from .output import Copied, FrozenFile, Merged, Output
from .surgery import apply_json, apply_toml


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
    if isinstance(content, FrozenFile | Copied):
        # A copy stays a reference and is streamed when written, never held whole.
        return {path: content}
    if isinstance(content, Merged):
        applied = created_document(path, content)
        return {**({path: applied} if applied is not None else {}), **dict(content.records)}
    return {path: content}


def _unchanged(path: Path, content: str | FrozenFile | Copied) -> bool:
    """Same bytes already there, so rewriting would only move the mtime."""
    if not path.is_file():
        return False
    if isinstance(content, Copied):
        return _same_copy(path, content)
    if isinstance(content, FrozenFile):
        return (
            path.read_bytes() == content.content
            and path.stat().st_mode & 0o7777 == content.mode & 0o7777
        )
    return path.read_bytes() == content.encode("utf-8")


def check_all(
    root: Path, profile: str = "default", outputs: Mapping[Path, Output] | None = None
) -> list[tuple[Path, str, str]]:
    """Every output on disk that differs from `outputs`, rendered here when not given."""
    root = root.absolute()
    outputs = render_all(root, profile) if outputs is None else outputs
    drift: list[tuple[Path, str, str]] = []
    for path, expected in outputs.items():
        if isinstance(expected, FrozenFile):
            if (
                not path.is_file()
                or path.read_bytes() != expected.content
                or path.stat().st_mode & 0o7777 != expected.mode
            ):
                drift.append(
                    (
                        path,
                        describe_file(path),
                        f"{len(expected.content)} bytes, mode {expected.mode:o}\n",
                    )
                )
            continue
        if isinstance(expected, Copied):
            if _copy_drifted(path, expected.source, expected.prefix):
                drift.append(
                    (path, describe_file(path), describe_file(expected.source, expected.prefix))
                )
            continue
        actual = path.read_text(encoding="utf-8", newline="") if path.is_file() else ""
        # Applying is identity on everything loadout does not own, so this compares
        # owned keys alone: the harness writing its own project table cannot read
        # as drift.
        wanted = applied_document(path, expected) if isinstance(expected, Merged) else expected
        if actual != wanted:
            drift.append((path, actual, wanted))
        if isinstance(expected, Merged):
            for record_path, text in expected.records:
                on_disk = record_path.read_text(encoding="utf-8") if record_path.is_file() else ""
                if on_disk != text:
                    drift.append((record_path, on_disk, text))
    return drift


CHUNK = 1 << 20


def _same_copy(path: Path, copied: Copied) -> bool:
    """Whether `path` already holds the copy, compared a chunk at a time."""
    have = path.stat()
    if stat.S_IMODE(have.st_mode) != copied.file_mode() & 0o7777:
        return False
    if have.st_size != len(copied.prefix) + copied.source.stat().st_size:
        return False
    with path.open("rb") as existing, copied.source.open("rb") as source:
        if existing.read(len(copied.prefix)) != copied.prefix:
            return False
        while True:
            chunk = source.read(CHUNK)
            if existing.read(CHUNK) != chunk:
                return False
            if not chunk:
                return True


def _atomic_stream_copy(path: Path, copied: Copied) -> None:
    def fill(handle: BinaryIO) -> None:
        handle.write(copied.prefix)
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


def _copy_drifted(path: Path, source: Path, prefix: bytes = b"") -> bool:
    if not path.is_file():
        return True
    if path.read_bytes() != prefix + source.read_bytes():
        return True
    return _executable(path) != _executable(source)


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def describe_file(path: Path, prefix: bytes = b"") -> str:
    # Drift on a copied file reports a summary, not the content: a tree carries
    # binaries, and a byte diff of one is noise rather than a review.
    if not path.is_file():
        return "(absent)\n"
    suffix = " (executable)" if _executable(path) else ""
    return f"{len(prefix) + path.stat().st_size} bytes{suffix}\n"
