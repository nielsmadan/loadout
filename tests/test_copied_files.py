"""A skill is a tree, so emit carries files it copies as well as files it renders.

These pin the two properties a `str` could not carry: the exact bytes of a
non-text file, and the exec bit. Three `scripts/` files in the live source are
executable, and a skill whose script arrives without its mode is broken in a way
byte-comparing its content would not reveal.
"""

from __future__ import annotations

from pathlib import Path

from loadout.emit import Copied, _copy_drifted, _executable, atomic_copy, describe_file


def _make(path: Path, content: bytes, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755 if executable else 0o644)
    return path


def test_copy_reproduces_bytes_exactly(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "asset.bin", b"\x00\x01\x02\xff\xfe")
    target = tmp_path / "out" / "asset.bin"
    target.parent.mkdir(parents=True)

    atomic_copy(target, source)

    assert target.read_bytes() == b"\x00\x01\x02\xff\xfe"


def test_copy_preserves_the_exec_bit(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "run.py", b"#!/usr/bin/env python\n", executable=True)
    target = tmp_path / "out" / "run.py"
    target.parent.mkdir(parents=True)

    atomic_copy(target, source)

    assert _executable(target)


def test_copy_leaves_a_plain_file_unexecutable(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "notes.md", b"# notes\n")
    target = tmp_path / "out" / "notes.md"
    target.parent.mkdir(parents=True)

    atomic_copy(target, source)

    assert not _executable(target)


def test_copy_writes_through_a_symlink(tmp_path: Path) -> None:
    """Same contract as atomic_write: a destination is often a symlink into the
    user's config repo, and replacing the link instead of its target silently
    detaches the file from the repo it was meant to land in."""
    source = _make(tmp_path / "src" / "asset.txt", b"payload\n")
    real = tmp_path / "repo" / "asset.txt"
    _make(real, b"stale\n")
    link = tmp_path / "out" / "asset.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    atomic_copy(link, source)

    assert link.is_symlink()
    assert real.read_bytes() == b"payload\n"


def test_drift_when_bytes_differ(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "a.txt", b"one\n")
    target = _make(tmp_path / "out" / "a.txt", b"two\n")

    assert _copy_drifted(target, source)


def test_drift_when_only_the_mode_differs(tmp_path: Path) -> None:
    """The bytes match and the file is still wrong — this is the case a text
    comparison cannot see."""
    source = _make(tmp_path / "src" / "run.py", b"echo hi\n", executable=True)
    target = _make(tmp_path / "out" / "run.py", b"echo hi\n", executable=False)

    assert _copy_drifted(target, source)


def test_no_drift_when_bytes_and_mode_match(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "run.py", b"echo hi\n", executable=True)
    target = _make(tmp_path / "out" / "run.py", b"echo hi\n", executable=True)

    assert not _copy_drifted(target, source)


def test_drift_when_the_target_is_absent(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "a.txt", b"one\n")

    assert _copy_drifted(tmp_path / "out" / "a.txt", source)


def test_describe_reports_size_and_mode(tmp_path: Path) -> None:
    plain = _make(tmp_path / "plain.txt", b"12345")
    runnable = _make(tmp_path / "run.sh", b"12345", executable=True)

    assert describe_file(plain) == "5 bytes\n"
    assert describe_file(runnable) == "5 bytes (executable)\n"
    assert describe_file(tmp_path / "missing.txt") == "(absent)\n"


def test_copied_carries_its_source(tmp_path: Path) -> None:
    assert Copied(source=tmp_path / "a").source == tmp_path / "a"
