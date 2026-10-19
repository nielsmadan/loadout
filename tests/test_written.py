"""The record of what sync wrote — ADR 0019's store.

Its whole safety argument is that it only ever *widens* acceptance, so the tests that
matter most are the degradation ones: every way of losing the record must reproduce
the behaviour that existed before it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from loadout import sync
from loadout.output import Copied
from loadout.written import (
    VERSION,
    WrittenEntry,
    accepts_bytes,
    accepts_text,
    every_recorded_path,
    normalise,
    owned_values,
    read_written,
    record_written,
    text_entry,
    written_state_path,
)


def entry(content: str = "hello\n") -> WrittenEntry:
    return text_entry(Path("out.md"), content)


def test_a_record_round_trips(tmp_path: Path) -> None:
    record_written(tmp_path, {Path("/dest/out.md"): entry()})

    read = read_written(tmp_path)

    assert set(read) == {Path("/dest/out.md")}
    assert read[Path("/dest/out.md")].norm_sha256 == entry().norm_sha256


def test_two_roots_do_not_share_a_record(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    assert written_state_path(a) != written_state_path(b)


def test_earlier_entries_survive_a_later_run(tmp_path: Path) -> None:
    """Retention is what makes this the orphan sidecar rather than a cache: a
    destination this run no longer renders is exactly what orphan removal needs."""
    record_written(tmp_path, {Path("/dest/first.md"): entry()})
    record_written(tmp_path, {Path("/dest/second.md"): entry()})

    assert set(read_written(tmp_path)) == {Path("/dest/first.md"), Path("/dest/second.md")}


def test_a_missing_record_reads_as_empty(tmp_path: Path) -> None:
    assert read_written(tmp_path) == {}


def test_a_corrupt_record_reads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    path = written_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    assert read_written(tmp_path) == {}


def test_a_record_that_is_not_utf8_reads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    path = written_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"version": \xff}')

    assert read_written(tmp_path) == {}


def test_every_recorded_path_names_each_roots_outputs(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    record_written(a, {Path("/dest/a.md"): entry()})
    record_written(b, {Path("/dest/b.md"): entry()})
    garbage = written_state_path(a).with_name("garbage.json")
    garbage.write_bytes(b"\xff")

    assert every_recorded_path([Path("/dest")]) == {Path("/dest/a.md"), Path("/dest/b.md")}


def test_a_future_version_reads_as_empty(tmp_path: Path) -> None:
    """A newer loadout's record must not be half-understood by an older one; an
    unreadable record has to mean 'no baseline', never 'accept anything'."""
    path = written_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 99, "written": {"/x": {"kind": "text"}}}), "utf-8")

    assert read_written(tmp_path) == {}


def test_acceptance_needs_an_entry(tmp_path: Path) -> None:
    """The guard clause the whole safety argument rests on: no entry, no acceptance."""
    assert not accepts_text(None, Path("out.md"), "hello\n")
    assert not accepts_bytes(None, b"hello\n")


def test_text_acceptance_ignores_what_the_normaliser_ignores(tmp_path: Path) -> None:
    """A harness reordering keys in a file it shares with loadout is not a hand edit,
    so the record compares the normalised form — the same one the accept set uses."""
    written = text_entry(Path("s.json"), json.dumps({"a": 1, "b": 2}))

    assert accepts_text(written, Path("s.json"), json.dumps({"b": 2, "a": 1}))
    assert not accepts_text(written, Path("s.json"), json.dumps({"a": 1, "b": 3}))


def test_normalise_is_the_one_the_guard_uses(tmp_path: Path) -> None:
    """Recorded here and compared in sync.py, so the two must be the same
    function — a record hashed with a different normaliser accepts nothing."""
    assert sync.normalise is normalise


def test_a_record_that_cannot_be_written_is_not_an_error(tmp_path: Path, monkeypatch) -> None:
    """The files are already on disk when this runs, so a failure here would report a
    completed sync as broken. A sandbox that grants the machine config file without
    the directory beside it is the case that forced this."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "denied"))
    (tmp_path / "denied").write_text("not a directory", encoding="utf-8")

    assert record_written(tmp_path, {Path("/dest/out.md"): entry()}) is None
    assert read_written(tmp_path) == {}


def test_copied_output_record_hashes_the_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_bytes(b"instructions\n")
    output = tmp_path / "output.md"
    entries = sync._entries_for({output: Copied(source)})
    assert entries[output].sha256 == hashlib.sha256(b"instructions\n").hexdigest()


def test_a_record_write_that_fails_leaves_the_last_record_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, 2.5: a torn record broke every later restore; the write is a rename."""
    record_written(tmp_path, {tmp_path / "a": WrittenEntry(kind="text", sha256="1")})
    before = written_state_path(tmp_path).read_text()

    def fail(*args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    assert record_written(tmp_path, {tmp_path / "b": WrittenEntry(kind="text", sha256="2")}) is None

    assert written_state_path(tmp_path).read_text() == before
    assert sorted(p.name for p in written_state_path(tmp_path).parent.iterdir()) == [
        written_state_path(tmp_path).name
    ]


def test_only_the_recorded_paths_inside_the_folders_asked_about_are_read(tmp_path: Path) -> None:
    """Performance: thirty foreign roots took a no-op sync from 0.22 s to 1.68 s, every
    entry of every record built though only managed folders are scanned."""
    record_written(
        tmp_path,
        {
            Path("/home/.claude/skills/a/SKILL.md"): entry(),
            Path("/home/.claude/skills-extra/b.md"): entry(),
            Path("/home/.codex/AGENTS.md"): entry(),
        },
    )

    assert every_recorded_path([Path("/home/.claude/skills")]) == {
        Path("/home/.claude/skills/a/SKILL.md")
    }


@pytest.mark.parametrize(
    ("text", "owned", "format_name"),
    [
        ("[]\n", (), "json"),
        ("[]\n", ("mcpServers",), "json"),
        ("tui = 5\n", ("tui.theme",), "toml"),
    ],
)
def test_a_document_that_cannot_hold_the_owned_keys_reads_as_unparsable(
    text: str, owned: tuple[str, ...], format_name: str
) -> None:
    """Review 3, batch 2.10: `[]` read as holding no keys, and a scalar parent as the key
    being absent, so the guard reported a removal instead."""
    assert owned_values(text, owned, format_name) is None


def test_a_record_entry_of_an_unknown_shape_is_dropped(tmp_path: Path) -> None:
    """Review 3, batch 4.4: an unknown kind was read as a copied file."""
    root = tmp_path / "p"
    root.mkdir()
    record = written_state_path(root)
    record.parent.mkdir(parents=True)
    kept = str(root / "kept.md")
    record.write_text(
        json.dumps(
            {
                "version": VERSION,
                "root": str(root),
                "written": {
                    kept: {"kind": "copied", "sha256": "abc"},
                    str(root / "bogus.md"): {"kind": "bogus", "sha256": "abc"},
                    str(root / "owned.json"): {"kind": "merged", "owned": "mcpServers"},
                    str(root / "hash.md"): {"kind": "text", "norm_sha256": 7},
                },
            }
        )
    )

    assert read_written(root) == {Path(kept): WrittenEntry(kind="copied", sha256="abc")}
