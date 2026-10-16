"""A staged blob is written out only when git delivered all of it (review 4, batch 2.11)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from loadout import git_runner
from loadout.errors import LoadoutError
from loadout.git_tree import Entry, write_snapshot

OID = "a" * 40


class _Process:
    def __init__(self, reply: bytes, code: int) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(reply)
        self.code = code

    def poll(self) -> int | None:
        return self.code

    def wait(self) -> int:
        return self.code

    def kill(self) -> None:
        pass


def _fake(monkeypatch: pytest.MonkeyPatch, reply: bytes, code: int = 0) -> None:
    monkeypatch.setattr(git_runner, "start", lambda *args, **kwargs: _Process(reply, code))


def test_a_whole_blob_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, f"{OID} blob 6\n".encode() + b"Rules\n\n")

    write_snapshot(tmp_path, tmp_path / "out", {"x.md": Entry("100644", OID)})

    assert (tmp_path / "out/x.md").read_bytes() == b"Rules\n"


def test_a_truncated_blob_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, f"{OID} blob 10\n".encode() + b"Rul")

    with pytest.raises(LoadoutError, match=r"cannot read staged blob: x\.md"):
        write_snapshot(tmp_path, tmp_path / "out", {"x.md": Entry("100644", OID)})


def test_a_failed_cat_file_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake(monkeypatch, f"{OID} blob 6\n".encode() + b"Rules\n\n", code=128)

    with pytest.raises(LoadoutError, match="git cat-file exited 128"):
        write_snapshot(tmp_path, tmp_path / "out", {"x.md": Entry("100644", OID)})


def test_a_reply_cut_before_its_separator_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake(monkeypatch, f"{OID} blob 6\n".encode() + b"Rules\n")

    with pytest.raises(LoadoutError, match=r"cannot read staged blob: x\.md"):
        write_snapshot(tmp_path, tmp_path / "out", {"x.md": Entry("100644", OID)})


class _Finishing(_Process):
    """Still running once its last reply is read, as git is until its input closes."""

    def __init__(self, reply: bytes) -> None:
        super().__init__(reply, 0)
        self.running = True

    def poll(self) -> int | None:
        return None if self.running else self.code

    def kill(self) -> None:
        self.code, self.running = -9, False


def test_a_cat_file_still_finishing_is_waited_for_not_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 4, batch 2.11: a kill after the last reply turned a whole snapshot into a
    failure whenever the writer had not yet closed git's input."""
    process = _Finishing(f"{OID} blob 6\n".encode() + b"Rules\n\n")
    monkeypatch.setattr(git_runner, "start", lambda *args, **kwargs: process)

    write_snapshot(tmp_path, tmp_path / "out", {"x.md": Entry("100644", OID)})

    assert ((tmp_path / "out/x.md").read_bytes(), process.code) == (b"Rules\n", 0)
