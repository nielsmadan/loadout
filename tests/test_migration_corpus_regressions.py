from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout.discovery import discover
from loadout.git_privacy import ignored_paths

pytestmark = pytest.mark.migration_integration


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def repository(root: Path) -> None:
    root.mkdir(exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")


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


def test_paths_in_many_directories_of_one_repository_share_one_probe(
    tmp_path: Path, monkeypatch
) -> None:
    """Review 2.8: one Git run per repository, not one per directory."""
    repository(tmp_path)
    paths = []
    for index in range(10):
        directory = tmp_path / f"d{index}"
        directory.mkdir()
        paths.append(directory / "f.txt")
        paths[-1].write_text("x\n")
    (tmp_path / ".gitignore").write_text("d3/\n")
    calls = []
    original = subprocess.run

    def run(command, *args, **kwargs):
        if "check-ignore" in command:
            calls.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)

    assert ignored_paths(paths) == {tmp_path / "d3/f.txt"}
    assert len(calls) == 1
