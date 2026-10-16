"""A skill is a tree, so emit carries files it copies as well as files it renders.

These pin the two properties a `str` could not carry: the exact bytes of a
non-text file, and the exec bit. Three `scripts/` files in the live source are
executable, and a skill whose script arrives without its mode is broken in a way
byte-comparing its content would not reveal.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import loadout
from loadout import output, sync
from loadout.emit import render_all
from loadout.files import copy_matches, describe_file, write_outputs
from loadout.output import Copied, FrozenFile, Merged, file_sha256
from loadout.sync import compare_with_disk
from registration import register


def _make(path: Path, content: bytes, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755 if executable else 0o644)
    return path


def test_copy_reproduces_bytes_exactly(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "asset.bin", b"\x00\x01\x02\xff\xfe")
    target = tmp_path / "out" / "asset.bin"
    target.parent.mkdir(parents=True)

    write_outputs({target: Copied(source)})

    assert target.read_bytes() == b"\x00\x01\x02\xff\xfe"


def test_copy_preserves_the_exec_bit(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "run.py", b"#!/usr/bin/env python\n", executable=True)
    target = tmp_path / "out" / "run.py"
    target.parent.mkdir(parents=True)

    write_outputs({target: Copied(source)})

    assert target.stat().st_mode & 0o111


def test_copy_leaves_a_plain_file_unexecutable(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "notes.md", b"# notes\n")
    target = tmp_path / "out" / "notes.md"
    target.parent.mkdir(parents=True)

    write_outputs({target: Copied(source)})

    assert not target.stat().st_mode & 0o111


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

    write_outputs({link: Copied(source)})

    assert link.is_symlink()
    assert real.read_bytes() == b"payload\n"


def test_drift_when_bytes_differ(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "a.txt", b"one\n")
    target = _make(tmp_path / "out" / "a.txt", b"two\n")

    assert not copy_matches(target, Copied(source))


def test_drift_when_only_the_mode_differs(tmp_path: Path) -> None:
    """The bytes match and the file is still wrong — this is the case a text
    comparison cannot see."""
    source = _make(tmp_path / "src" / "run.py", b"echo hi\n", executable=True)
    target = _make(tmp_path / "out" / "run.py", b"echo hi\n", executable=False)

    assert not copy_matches(target, Copied(source))


def test_no_drift_when_bytes_and_mode_match(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "run.py", b"echo hi\n", executable=True)
    target = _make(tmp_path / "out" / "run.py", b"echo hi\n", executable=True)

    assert copy_matches(target, Copied(source))


def test_drift_when_the_target_is_absent(tmp_path: Path) -> None:
    source = _make(tmp_path / "src" / "a.txt", b"one\n")

    assert not copy_matches(tmp_path / "out" / "a.txt", Copied(source))


def test_describe_reports_size_and_mode(tmp_path: Path) -> None:
    plain = _make(tmp_path / "plain.txt", b"12345")
    runnable = _make(tmp_path / "run.sh", b"12345", executable=True)

    digest = hashlib.sha256(b"12345").hexdigest()
    assert (
        describe_file(plain) == f"5 bytes, mode {plain.stat().st_mode & 0o777:o}, sha256 {digest}\n"
    )
    assert describe_file(runnable) == f"5 bytes, mode 755, sha256 {digest}\n"
    assert describe_file(tmp_path / "missing.txt") == "(absent)\n"


def test_copied_carries_its_source(tmp_path: Path) -> None:
    assert Copied(source=tmp_path / "a").source == tmp_path / "a"


@pytest.mark.integration
def test_check_the_preview_and_sync_agree_on_a_copy_s_mode(tmp_path: Path, capsys) -> None:
    """Review 2, 2.7: after `chmod 600`, check saw no drift, the preview an update and
    sync a rewrite, because each judged a copy its own way."""
    root = tmp_path / "p"
    skill = root / "loadout/skills/s"
    skill.mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nS.\n")
    (skill / "notes.txt").write_text("notes\n")
    (skill / "notes.txt").chmod(0o644)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    copy = root / ".claude/skills/s/notes.txt"
    copy.chmod(0o600)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1
    assert "DRIFT (source-changed): .claude/skills/s/notes.txt" in capsys.readouterr().err
    assert [c.kind for c in compare_with_disk(render_all(root)) if c.path == copy] == ["update"]
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert copy.stat().st_mode & 0o777 == 0o644
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_a_rewritten_source_is_hashed_again(tmp_path: Path) -> None:
    """A digest is read from the file each time it is asked for, outside a pass's memo."""
    source = tmp_path / "asset.bin"
    source.write_bytes(b"first")
    first = file_sha256(source)
    source.write_bytes(b"other")

    assert first == hashlib.sha256(b"first").hexdigest()
    assert file_sha256(source) == hashlib.sha256(b"other").hexdigest()


def test_sync_never_holds_a_copied_file_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance: one 40 MB asset took a no-op sync from 35 MB to 119 MB."""
    source = tmp_path / "asset.bin"
    source.write_bytes(b"payload")
    outputs = {
        tmp_path / "a" / "asset.bin": Copied(source),
        tmp_path / "b" / "asset.bin": Copied(source),
    }
    write_outputs(outputs)

    def whole(self: Path) -> bytes:
        raise AssertionError(f"read whole: {self}")

    monkeypatch.setattr(Path, "read_bytes", whole)

    assert [change.kind for change in compare_with_disk(outputs)] == ["identical", "identical"]
    assert sync.unaccepted_outputs(sync.sync_state(tmp_path), outputs) == []
    digest = hashlib.sha256(b"payload").hexdigest()
    assert {entry.sha256 for entry in sync._entries_for(outputs).values()} == {digest}


def test_a_merged_outputs_ownership_record_is_compared_too(tmp_path: Path) -> None:
    """Review 2, batch 5 8: the preview has to show the record a shared document brings."""
    record = tmp_path / "config.owned"
    outputs = {
        tmp_path / "config.toml": Merged(
            frozenset({"model"}), 'model = "x"\n', records=((record, "model\n"),)
        )
    }

    changes = {change.path: change.kind for change in compare_with_disk(outputs)}

    assert changes == {tmp_path / "config.toml": "create", record: "create"}


def test_a_frozen_outputs_record_hashes_its_bytes(tmp_path: Path) -> None:
    path = tmp_path / "hook"

    entries = sync._entries_for({path: FrozenFile(b"#!/bin/sh\n", 0o755)})

    assert entries[path].sha256 == hashlib.sha256(b"#!/bin/sh\n").hexdigest()


def test_a_source_copied_to_several_harnesses_is_hashed_once_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, batch 3.4: a module-wide cache keyed by a resolve and a stat cost about
    what it saved, never cleared, and could go stale on a coarse clock. The run keeps
    its own memo instead."""
    root = register(tmp_path / "source")
    skill = root / "skills/x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\nX\n")
    (skill / "run.sh").write_text("true\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\n\n[codex]\n\n[droid]\n'
    )
    outputs = render_all(root)
    script = skill / "run.sh"
    copies = [p for p, o in outputs.items() if isinstance(o, Copied) and o.source == script]
    assert len(copies) >= 2
    write_outputs(outputs)
    hashed: list[Path] = []
    real = output.file_sha256

    def counting(path: Path) -> str:
        hashed.append(path)
        return real(path)

    monkeypatch.setattr(output, "file_sha256", counting)

    sync.unaccepted_outputs(sync.sync_state(root), outputs)
    guarded = hashed.count(script)
    hashed.clear()
    sync._entries_for(outputs)

    assert (guarded, hashed.count(script)) == (1, 1)


def test_a_sync_reads_each_copied_source_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 4, batch 3.4: in a sync with nothing to write, the guard, the writer and the
    record each read it again."""
    root = register(tmp_path / "source")
    skill = root / "skills/x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: x\ndescription: d\n---\nX\n")
    script = skill / "run.sh"
    script.write_text("true\n")
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n\n[claude]\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    reads: list[Path] = []
    real_open = Path.open

    def recording(self: Path, *args: object, **kwargs: object) -> object:
        if self == script:
            reads.append(self)
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "open", recording)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert len(reads) == 1
