"""The record of what sync wrote — ADR 0019's store.

Its whole safety argument is that it only ever *widens* acceptance, so the tests that
matter most are the degradation ones: every way of losing the record must reproduce
the behaviour that existed before it.
"""

from __future__ import annotations

import json
from pathlib import Path

from loadout import commands
from loadout.written import (
    WrittenEntry,
    accepts_bytes,
    accepts_text,
    normalise,
    read_written,
    record_written,
    text_entry,
    written_state_path,
)


def entry(content: str = "hello\n") -> WrittenEntry:
    return text_entry(Path("out.md"), content)


def test_a_record_round_trips(tmp_path: Path) -> None:
    record_written(tmp_path, "default", {Path("/dest/out.md"): entry()})

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
    record_written(tmp_path, "default", {Path("/dest/first.md"): entry()})
    record_written(tmp_path, "default", {Path("/dest/second.md"): entry()})

    assert set(read_written(tmp_path)) == {Path("/dest/first.md"), Path("/dest/second.md")}


def test_a_missing_record_reads_as_empty(tmp_path: Path) -> None:
    assert read_written(tmp_path) == {}


def test_a_corrupt_record_reads_as_empty_rather_than_raising(tmp_path: Path) -> None:
    path = written_state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")

    assert read_written(tmp_path) == {}


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
    """Recorded here and compared in commands.py, so the two must be the same
    function — a record hashed with a different normaliser accepts nothing."""
    assert commands.normalise is normalise
