"""The one atomic writer every file loadout writes goes through (review 3, batch 4.4)."""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path
from typing import BinaryIO

import pytest

from loadout import atomic
from loadout.atomic import atomic_install, read_file, replace_atomically, temporary_name
from loadout.errors import LoadoutError
from loadout.output import FrozenFile


def test_a_write_that_fails_leaves_the_target_and_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text("old\n")

    def fail(handle: BinaryIO) -> None:
        handle.write(b"half")
        raise RuntimeError("killed")

    with pytest.raises(RuntimeError):
        replace_atomically(target, fail)

    assert (target.read_text(), sorted(p.name for p in tmp_path.iterdir())) == (
        "old\n",
        ["doc.json"],
    )


def test_an_install_checks_for_a_link_again_just_before_the_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link put in the way while the file was being written is still refused."""
    target = tmp_path / "hook"
    target.write_text("old\n")
    calls: list[Path] = []

    def refuse_second(path: Path, root: Path | None = None) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise LoadoutError(f"path is a symlink: {path}")

    monkeypatch.setattr(atomic, "no_symlinks", refuse_second)

    with pytest.raises(LoadoutError, match="path is a symlink"):
        atomic_install(target, FrozenFile(b"new\n", 0o755), tmp_path)

    assert (target.read_text(), len(calls)) == ("old\n", 2)


def test_an_interrupted_write_leaves_no_temporary_file(tmp_path: Path) -> None:
    """Review 6, batch 3 (at-baseexc): only an `Exception` was cleaned up after."""
    target = tmp_path / "doc.json"

    def interrupt(handle: BinaryIO) -> None:
        handle.write(b"half")
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        replace_atomically(target, interrupt)

    assert list(tmp_path.iterdir()) == []


def test_only_the_exact_shape_of_a_write_in_progress_is_one() -> None:
    """Review 6, batch 3 (at-tmp-regex-any, at-tmp-fullmatch): a user's file of any
    other shape is never swept."""
    names = [".loadout-tmp-k1ll3d0_", ".loadout-tmp-abc", ".loadout-tmp-xxxxxxxx.bak"]

    assert [temporary_name(name) for name in names] == [True, False, False]


def test_a_file_read_through_a_link_is_refused(tmp_path: Path) -> None:
    """Review 6, batch 3 (at-readfile-links)."""
    (tmp_path / "real").write_text("x\n")
    (tmp_path / "link").symlink_to(tmp_path / "real")

    with pytest.raises(LoadoutError) as error:
        read_file(tmp_path / "link")

    assert str(error.value) == f"path is a symlink: {tmp_path / 'link'}"


def test_a_file_under_a_file_is_refused(tmp_path: Path) -> None:
    """Review 6, batch 3 (at-parentdir2)."""
    (tmp_path / "plain").write_text("x\n")

    with pytest.raises(LoadoutError) as error:
        read_file(tmp_path / "plain/child")

    assert str(error.value) == f"parent must be a directory: {tmp_path / 'plain'}"


def _feed(fifo: Path) -> None:
    with contextlib.suppress(BrokenPipeError):
        fifo.write_bytes(b"data")


def test_a_fifo_is_refused_rather_than_read(tmp_path: Path) -> None:
    """Review 6, batch 3 (at-isreg): reading one blocks until a writer comes."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    # A writer, so a read that wrongly opens the pipe returns rather than hangs.
    writer = threading.Thread(target=_feed, args=(fifo,), daemon=True)
    writer.start()

    try:
        with pytest.raises(LoadoutError) as error:
            read_file(fifo)
    finally:
        os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
        writer.join(timeout=5)

    assert str(error.value) == f"path must be a regular file: {fifo}"
