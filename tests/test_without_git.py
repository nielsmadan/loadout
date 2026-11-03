"""Loadout runs without git on PATH: the guard does not need it, and `check --staged`,
which does, says so (review 5, batch 2.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout import git_runner

pytestmark = pytest.mark.integration


@pytest.fixture
def no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "no-git-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    git_runner.local_variables.cache_clear()


def _hand_edited_project(root: Path) -> Path:
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (root / "loadout/instructions/main.md").write_text("Rules.\n")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / "AGENTS.md").write_text("Hand-written.\n")
    return root


@pytest.mark.parametrize(("command", "code"), [(["sync"], 1), (["check"], 1)])
def test_the_guard_refuses_without_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, command: list[str], code: int
) -> None:
    root = _hand_edited_project(tmp_path / "p")
    empty = tmp_path / "no-git-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    git_runner.local_variables.cache_clear()
    capsys.readouterr()

    assert loadout.main([*command, "--root", str(root)]) == code

    assert "AGENTS.md" in capsys.readouterr().err
    assert (root / "AGENTS.md").read_text() == "Hand-written.\n"


@pytest.mark.parametrize(
    "command",
    [
        ["check", "--staged"],
        ["integrate", "git-hooks", "install"],
        ["integrate", "git-hooks", "status"],
    ],
)
def test_a_command_that_needs_git_says_it_is_missing(
    tmp_path: Path, no_git: None, capsys, command: list[str]
) -> None:
    root = tmp_path / "p"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\n')

    assert loadout.main([*command, "--root", str(root)]) == 3

    assert capsys.readouterr().err == "loadout: git is not on PATH; install git or put it on PATH\n"
