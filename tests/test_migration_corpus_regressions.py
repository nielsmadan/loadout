from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loadout import migration_journal
from loadout.artifacts import load_artifacts
from loadout.commands import cmd_sync
from loadout.discovery import discover
from loadout.errors import LoadoutError
from loadout.git_privacy import ignored_paths
from loadout.migration import plan_migration
from loadout.migration_transaction import apply_migration, prepare_migration, recover_migration


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def repository(root: Path) -> None:
    root.mkdir(exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")


@pytest.mark.parametrize(
    ("name", "mode"), [("CLAUDE.md", 0o640), (".claude/skills/tool/run.sh", 0o750)]
)
def test_staged_source_reconstructs_full_opaque_mode(tmp_path: Path, name: str, mode: int) -> None:
    root = tmp_path / "original"
    repository(root)
    original = root / name
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(b"inert exact bytes\r\n")
    original.chmod(mode)
    plan = plan_migration(discover(root, scope="project", agents=("claude",)))
    assert plan.complete, plan.preview()
    apply_migration(prepare_migration(plan))
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(root, "checkout-index", "--all", "--prefix=" + str(fresh) + "/")
    assert cmd_sync(fresh) == 0
    assert (fresh / name).read_bytes() == b"inert exact bytes\r\n"
    assert (fresh / name).stat().st_mode & 0o777 == mode


def test_many_siblings_share_privacy_probe(tmp_path: Path, monkeypatch) -> None:
    repository(tmp_path)
    source = tmp_path / ".claude/skills/tool"
    source.mkdir(parents=True)
    for index in range(25):
        (source / f"{index}.bin").write_bytes(b"inert")
    (tmp_path / ".gitignore").write_text(".claude/skills/tool/\n")
    calls = []
    original = subprocess.run

    def run(command, *args, **kwargs):
        if "check-ignore" in command:
            calls.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    inventory = discover(tmp_path, scope="project", agents=("claude",))
    migrated = [
        candidate for candidate in inventory.candidates if candidate.disposition == "migrated"
    ]
    assert len(migrated) == 25
    assert all(candidate.private and candidate.personal for candidate in migrated)
    assert 0 < len(calls) <= 4


def test_batched_privacy_keeps_nested_repositories_and_rechecks(tmp_path: Path) -> None:
    repository(tmp_path)
    nested = tmp_path / "nested"
    repository(nested)
    (tmp_path / ".gitignore").write_text("*.txt\n")
    outer = tmp_path / "outer.txt"
    inner = nested / "inner.txt"
    outer.touch()
    inner.touch()
    assert ignored_paths((outer, inner)) == {outer}
    (nested / ".gitignore").write_text("inner.txt\n")
    assert ignored_paths((outer, inner)) == {outer, inner}


def migrated_journal(root: Path):
    repository(root)
    (root / "CLAUDE.md").write_bytes(b"original instructions\n")
    plan = plan_migration(discover(root, scope="project", agents=("claude",)))
    result = apply_migration(prepare_migration(plan))
    return migration_journal.Journal.load(result.journal)


def test_cursor_saves_keep_payload_and_metadata_changes_replace_it(
    tmp_path: Path, monkeypatch
) -> None:
    journal = migrated_journal(tmp_path)
    journal.save()
    writes = []
    original = migration_journal.atomic_install

    def install(path, content):
        writes.append(path.name)
        original(path, content)

    monkeypatch.setattr(migration_journal, "atomic_install", install)
    journal.save()
    journal.save()
    assert writes == ["journal.json", "journal.json"]
    journal.metadata["recovered_operations"] = []
    journal.save()
    assert writes[-2].startswith("payload-")
    assert writes[-1] == "journal.json"
    assert migration_journal.Journal.load(journal.path).metadata["recovered_operations"] == []


@pytest.mark.parametrize("corruption", ["missing", "content", "public", "symlink"])
def test_journal_payload_corruption_blocks_recovery(tmp_path: Path, corruption: str) -> None:
    journal = migrated_journal(tmp_path)
    cursor = json.loads(journal.path.read_bytes())
    payload = journal.path.parent / f"payload-{cursor['payload']}.json"
    if corruption == "missing":
        payload.unlink()
    elif corruption == "content":
        payload.write_bytes(b"{}\n")
    elif corruption == "public":
        payload.chmod(0o644)
    else:
        other = payload.with_suffix(".original")
        payload.rename(other)
        payload.symlink_to(other)
    with pytest.raises(LoadoutError):
        recover_migration(journal.path)
    assert (tmp_path / "loadout/config.toml").is_file()


def test_version_one_journal_still_recovers(tmp_path: Path) -> None:
    journal = migrated_journal(tmp_path)
    journal.path.write_text(
        json.dumps(
            {
                "version": 1,
                "operations": [o.document() for o in journal.operations],
                "metadata": journal.metadata,
                "next": journal.next,
                "pending": journal.pending,
                "status": journal.status,
            }
        )
    )
    result = recover_migration(journal.path)
    assert result.conflicts == ()
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"original instructions\n"
    assert not (tmp_path / "loadout/config.toml").exists()


@pytest.mark.parametrize(
    "declaration",
    [
        "mode = true",
        "mode = -1",
        "mode = 4096",
        'modes = {"../escape" = 420}',
        'modes = {"nested/file" = false}',
    ],
)
def test_invalid_authored_modes_are_rejected(tmp_path: Path, declaration: str) -> None:
    index = tmp_path / "artifacts.toml"
    format_name = "tree" if declaration.startswith("modes") else "copy"
    index.write_text(
        f'[[artifact]]\nagents = ["claude"]\nformat = "{format_name}"\noutput = "CLAUDE.md"\ncategory = "instructions"\nsource = "source"\n{declaration}\n'
    )
    with pytest.raises(LoadoutError):
        load_artifacts(index, scope="project")
