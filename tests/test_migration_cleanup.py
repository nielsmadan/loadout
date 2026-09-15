from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout import migration_journal
from loadout.cli import main
from loadout.errors import LoadoutError
from loadout.migration_journal import Journal
from test_migration_recovery import repeated_path_journal

pytestmark = pytest.mark.migration_integration


def test_payload_replacement_keeps_the_previous_cursor_readable_until_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = repeated_path_journal(tmp_path)
    original = json.loads(journal.path.read_bytes())["payload"]
    install = migration_journal.atomic_install

    def interrupt(path, content):
        if path == journal.path:
            raise KeyboardInterrupt()
        install(path, content)

    journal.metadata["note"] = "updated"
    with monkeypatch.context() as patch:
        patch.setattr(migration_journal, "atomic_install", interrupt)
        with pytest.raises(KeyboardInterrupt):
            journal.save()
    assert json.loads(journal.path.read_bytes())["payload"] == original
    assert Journal.load(journal.path).operations == journal.operations
    assert len(list(journal.path.parent.glob("payload-*.json"))) == 2
    journal.save()
    current = json.loads(journal.path.read_bytes())["payload"]
    assert current != original
    assert [p.name for p in journal.path.parent.glob("payload-*.json")] == [
        f"payload-{current}.json"
    ]
    assert Journal.load(journal.path).metadata["note"] == "updated"


@pytest.mark.parametrize("status", ["apply", "recovering", "recovery-conflicts"])
def test_unfinished_journal_cannot_be_discarded(tmp_path: Path, status: str) -> None:
    journal = repeated_path_journal(tmp_path)
    journal.status = status
    journal.save()
    before = {p.name: p.read_bytes() for p in journal.path.parent.iterdir()}
    with pytest.raises(LoadoutError, match="unfinished"):
        journal.discard()
    assert {p.name: p.read_bytes() for p in journal.path.parent.iterdir()} == before


@pytest.mark.parametrize("status", ["complete", "apply", "recovered"])
@pytest.mark.parametrize(
    "fault",
    [
        ("payload", OSError),
        ("journal", OSError),
        ("payload", KeyboardInterrupt),
        ("journal", KeyboardInterrupt),
    ],
)
def test_cli_cleanup_failure_retains_its_journal_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    status: str,
    fault: tuple[str, type[BaseException]],
) -> None:
    boundary, failure = fault
    journal = repeated_path_journal(tmp_path)
    journal.metadata.update(additions=[], already_initialized=False)
    if status == "recovered":
        assert journal.recover() == ()
    journal.status = status
    journal.save()
    receipt = tmp_path / "loadout/.loadout-state/project.json"
    receipt.parent.mkdir()
    receipt.write_bytes(b"retained receipt\n")
    neighbor = journal.path.parent.parent / "other/journal.json"
    neighbor.parent.mkdir(mode=0o700)
    neighbor.write_bytes(b"retained transaction\n")
    unlink = Path.unlink

    def interrupt(path: Path, *args, **kwargs):
        if path.parent == journal.path.parent and (
            path.name.startswith("payload-") if boundary == "payload" else path == journal.path
        ):
            unlink(path, *args, **kwargs)
            raise failure("cleanup interrupted")
        return unlink(path, *args, **kwargs)

    action = "resume" if status == "complete" else "recover"
    args = ["init", f"--{action}", str(journal.path), "--yes", "--json"]
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", interrupt)
        assert main(args) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "interrupted", "journal": str(journal.path)}
    assert "cleanup interrupted" in output.err
    assert f"Resume: loadout init --resume {journal.path} --yes" in output.err
    assert f"Recover: loadout init --recover {journal.path} --yes" in output.err
    terminal = "complete" if action == "resume" else "recovered"
    assert Journal.load(journal.path).status == terminal
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == terminal
    assert result["journal"] is None
    assert not journal.path.parent.exists()
    assert receipt.read_bytes() == b"retained receipt\n"
    assert neighbor.read_bytes() == b"retained transaction\n"


def test_cleanup_refuses_a_symlink_inside_the_transaction(tmp_path: Path) -> None:
    journal = repeated_path_journal(tmp_path)
    journal.status = "complete"
    journal.save()
    outside = tmp_path / "outside"
    outside.write_bytes(b"user file\n")
    link = journal.path.parent / "unexpected"
    link.symlink_to(outside)
    with pytest.raises(LoadoutError, match="symlink"):
        journal.discard()
    assert outside.read_bytes() == b"user file\n"
    assert Journal.load(journal.path).status == "complete"
