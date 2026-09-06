from __future__ import annotations

import base64
import json
from itertools import pairwise
from pathlib import Path

import pytest

from loadout import migration_journal
from loadout.discovery import discover
from loadout.errors import LoadoutError
from loadout.migration import plan_migration
from loadout.migration_journal import Image, Journal, Operation
from loadout.migration_transaction import (
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)
from test_migration_corpus_regressions import git, migrated_journal, repository


def payloads(journal: Journal) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in journal.path.parent.glob("payload-*.json")}


def repeated_path_journal(root: Path) -> Journal:
    directory = root
    for name in (".loadout-state", "migrations", "recovery"):
        directory /= name
        directory.mkdir(mode=0o700)
    source = root / "loadout"
    source.mkdir()
    target = source / "source.txt"
    images = [Image(), *(Image("file", f"step {i}\n".encode(), 0o640) for i in range(3))]
    migration_journal.install(target, images[-1])
    journal = Journal(
        directory / "journal.json",
        tuple(Operation(target, before, after, "source") for before, after in pairwise(images)),
        {
            "root": str(root),
            "source_root": str(source),
            "destinations": [],
            "registration": [],
            "outputs": {},
            "git": None,
            "baseline": None,
        },
        next=3,
        status="apply",
    )
    journal.save()
    return journal


def migrated_support_journal(root: Path) -> tuple[Journal, dict[Path, Image]]:
    repository(root)
    unrelated = root / "notes.txt"
    unrelated.write_bytes(b"committed\n")
    git(root, "add", "notes.txt")
    git(root, "commit", "-qm", "original")
    unrelated.write_bytes(b"staged\n")
    git(root, "add", "notes.txt")
    unrelated.write_bytes(b"working\n")
    originals = {root / "CLAUDE.md": Image("file", b"original instructions\r\n", 0o640)}
    support = root / ".claude/skills/tool"
    support.mkdir(parents=True)
    for index in range(25):
        originals[support / f"{index}.bin"] = Image("file", bytes([index]) * 1024, 0o750)
    for path, image in originals.items():
        migration_journal.install(path, image)
    plan = plan_migration(discover(root, scope="project", agents=("claude",)))
    assert plan.complete, plan.preview()
    result = apply_migration(prepare_migration(plan))
    assert result.journal is not None
    return Journal.load(result.journal), originals


def test_recovery_payloads_stay_bounded_through_interruption_and_conflict_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, originals = migrated_support_journal(tmp_path)
    baseline = git(tmp_path, "rev-parse", "HEAD")
    expected_index = base64.b64decode(journal.metadata["refreshed_index"])
    retained = payloads(journal)
    measurements = []
    save, install = Journal.save, migration_journal.install
    restored = 0

    def measure(current: Journal) -> None:
        save(current)
        current_payloads = payloads(current)
        measurements.append((len(current_payloads), sum(map(len, current_payloads.values()))))

    def interrupt(path: Path, image: Image) -> None:
        nonlocal restored
        install(path, image)
        restored += 1
        if restored == 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(Journal, "save", measure)
    with monkeypatch.context() as patch:
        patch.setattr(migration_journal, "install", interrupt)
        with pytest.raises(KeyboardInterrupt):
            recover_migration(journal.path)
    assert restored == 3
    assert measurements
    expected_size = (len(retained), sum(map(len, retained.values())))
    assert set(measurements) == {expected_size}
    assert (tmp_path / ".git/index").read_bytes() == expected_index
    reloaded = Journal.load(journal.path)
    assert reloaded.status == "recovering"
    assert len(reloaded.recovered_operations) == 2
    conflict = next(o for o in journal.operations if o.path == tmp_path / "loadout/config.toml")
    conflict.path.write_bytes(b"concurrent source edit\n")
    conflicted = recover_migration(journal.path)
    assert conflict.path in conflicted.conflicts
    assert conflict.path.read_bytes() == b"concurrent source edit\n"
    assert Journal.load(journal.path).status == "recovery-conflicts"
    install(conflict.path, conflict.after)
    assert recover_migration(journal.path).conflicts == ()
    assert recover_migration(journal.path).conflicts == ()
    assert set(measurements) == {expected_size}
    assert payloads(journal) == retained
    assert Journal.load(journal.path).recovered_operations == set(range(journal.next))
    for path, image in originals.items():
        assert migration_journal.snapshot(path) == image
    assert not (tmp_path / "loadout/config.toml").exists()
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert (tmp_path / ".git/index").read_bytes() == expected_index
    assert git(tmp_path, "show", ":notes.txt") == b"staged\n"
    assert (tmp_path / "notes.txt").read_bytes() == b"working\n"


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_recovery_progress_survives_reload(
    version: int, tmp_path: Path, monkeypatch
) -> None:
    journal = repeated_path_journal(tmp_path)
    operation = journal.operations[2]
    migration_journal.install(operation.path, operation.before)
    journal.metadata["recovered_operations"] = [2]
    journal.save()
    if version == 1:
        raw = {
            "version": 1,
            "operations": [operation.document() for operation in journal.operations],
            "metadata": journal.metadata,
            "next": journal.next,
            "pending": journal.pending,
            "status": journal.status,
        }
    else:
        raw = json.loads(journal.path.read_bytes())
        raw.pop("recovered_operations", None)
    journal.path.write_text(json.dumps(raw))
    save = Journal.save

    def interrupt(current: Journal) -> None:
        save(current)
        if migration_journal.snapshot(operation.path) == journal.operations[1].before:
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(Journal, "save", interrupt)
        with pytest.raises(KeyboardInterrupt):
            recover_migration(journal.path)
    reloaded = Journal.load(journal.path)
    assert reloaded.recovered_operations == {1, 2}
    assert reloaded.metadata["recovered_operations"] == [2]
    retained = payloads(reloaded)
    assert recover_migration(journal.path).conflicts == ()
    assert migration_journal.snapshot(operation.path) == Image()
    assert payloads(reloaded) == retained


@pytest.mark.parametrize("location", ["cursor", "legacy"])
@pytest.mark.parametrize("invalid", [None, True, {}, "0", [True], [1.0], ["1"], [-1], [3], [1, 1]])
def test_invalid_recovery_progress_is_rejected(tmp_path: Path, location: str, invalid) -> None:
    journal = repeated_path_journal(tmp_path)
    if location == "legacy":
        journal.metadata["recovered_operations"] = invalid
        journal.save()
    else:
        raw = json.loads(journal.path.read_bytes())
        raw["recovered_operations"] = invalid
        journal.path.write_text(json.dumps(raw))
    before = migration_journal.snapshot(journal.operations[0].path)
    with pytest.raises(LoadoutError, match=r"recovery.*cursor"):
        recover_migration(journal.path)
    assert migration_journal.snapshot(journal.operations[0].path) == before


@pytest.mark.parametrize("pending", [False, True])
def test_recovery_progress_is_bounded_by_applied_operations(tmp_path: Path, pending: bool) -> None:
    journal = repeated_path_journal(tmp_path)
    raw = json.loads(journal.path.read_bytes())
    raw.update(next=1, pending=pending, recovered_operations=[1])
    journal.path.write_text(json.dumps(raw))
    if pending:
        assert Journal.load(journal.path).recovered_operations == {1}
    else:
        with pytest.raises(LoadoutError, match=r"recovery.*cursor"):
            Journal.load(journal.path)


@pytest.mark.parametrize("foreign_index", [False, True])
def test_interrupted_recovery_accepts_only_its_restored_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreign_index: bool
) -> None:
    journal = migrated_journal(tmp_path)
    baseline = git(tmp_path, "rev-parse", "HEAD")
    index = tmp_path / ".git/index"
    final_index = index.read_bytes()

    def interrupt(path: Path, image: Image) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(migration_journal, "install", interrupt)
        with pytest.raises(KeyboardInterrupt):
            recover_migration(journal.path)
    assert index.read_bytes() == base64.b64decode(journal.metadata["refreshed_index"])
    assert index.read_bytes() != final_index
    with pytest.raises(LoadoutError, match="must be planned again"):
        resume_migration(journal.path)
    if foreign_index:
        (tmp_path / "foreign.txt").write_bytes(b"user staging\n")
        git(tmp_path, "add", "foreign.txt")
        edited_index = index.read_bytes()
        source = (tmp_path / "loadout/config.toml").read_bytes()
        assert recover_migration(journal.path).conflicts == (index,)
        assert index.read_bytes() == edited_index
        assert (tmp_path / "loadout/config.toml").read_bytes() == source
    else:
        assert recover_migration(journal.path).conflicts == ()
        assert (tmp_path / "CLAUDE.md").read_bytes() == b"original instructions\n"
    assert git(tmp_path, "rev-parse", "HEAD") == baseline


def test_complete_journal_refuses_restored_index_without_recovery_intent(tmp_path: Path) -> None:
    journal = migrated_journal(tmp_path)
    index = tmp_path / ".git/index"
    source = (tmp_path / "loadout/config.toml").read_bytes()
    index.write_bytes(base64.b64decode(journal.metadata["refreshed_index"]))
    assert recover_migration(journal.path).conflicts == (index,)
    assert Journal.load(journal.path).status == "complete"
    assert (tmp_path / "loadout/config.toml").read_bytes() == source


def test_recovery_saves_already_restored_step_before_preceding_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = repeated_path_journal(tmp_path)
    operation = journal.operations[2]
    install = migration_journal.install
    install(operation.path, operation.before)

    def interrupt(path: Path, image: Image) -> None:
        install(path, image)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(migration_journal, "install", interrupt)
        with pytest.raises(KeyboardInterrupt):
            recover_migration(journal.path)
    assert Journal.load(journal.path).recovered_operations == {2}
    assert migration_journal.snapshot(operation.path) == journal.operations[1].before
    assert recover_migration(journal.path).conflicts == ()
    assert migration_journal.snapshot(operation.path) == Image()
