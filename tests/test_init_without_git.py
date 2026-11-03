"""Init without git on PATH says so, and what needs it, before it plans anything.

`init project` used to say it "could not verify original source privacy", and `init global`
failed part way and recommended the /loadout skill, which cannot install git.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout import git_runner

pytestmark = pytest.mark.migration_integration


@pytest.fixture
def no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    git_runner.local_variables.cache_clear()


def _repository(root: Path) -> Path:
    (root / ".claude").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# P\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def test_init_project_in_a_repository_says_git_is_missing(
    tmp_path: Path, capsys, request: pytest.FixtureRequest
) -> None:
    root = _repository(tmp_path / "p")
    request.getfixturevalue("no_git")

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--dry-run", "--json"]
    assert loadout.main(argv) == 3
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["error"] == (
        f"git is not on PATH; init project needs it to read how Git treats {root}'s files. "
        "Install git or put it on PATH, then run init again"
    )
    assert "recommendation" not in envelope


def test_init_global_says_git_is_missing(
    tmp_path: Path, fake_home: Path, capsys, no_git: None
) -> None:
    (fake_home / ".claude").mkdir()
    source = tmp_path / "agent-config"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 3
    assert capsys.readouterr().err == (
        "loadout: git is not on PATH; init global needs it to keep the source in a Git "
        "repository. Install git or put it on PATH, then run init again\n"
    )
    assert list(source.iterdir()) == []


def test_init_project_outside_a_repository_needs_no_git(
    tmp_path: Path, capsys, no_git: None
) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# P\n")

    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"]) == 0
    )
    assert (root / "loadout/instructions/main.md").read_text() == "# P\n"
