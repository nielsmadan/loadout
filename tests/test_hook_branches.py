"""The hooks let a branch with nothing of loadout's on it be checked out and committed to
(review 5, batch 2.4): one from before adoption, or one without the source folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.git_hook_commands import _nothing_to_protect
from registration import register
from test_worktree_hooks import _git, _global_source, _hooks, _loadout_on_path  # noqa: F401

pytestmark = pytest.mark.integration


def _commit(root: Path, message: str) -> None:
    assert _git(root, "add", "-A").returncode == 0
    assert _git(root, "commit", "-qm", message).returncode == 0


def test_a_project_branch_from_before_adoption_checks_out_and_commits(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "README.md").write_text("Before.\n")
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _commit(root, "before")
    assert _git(root, "branch", "old").returncode == 0
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (root / "loadout/instructions/main.md").write_text("Rules.\n")
    _commit(root, "adopt")
    _hooks(root)

    checked_out = _git(root, "checkout", "-q", "old")

    assert checked_out.returncode == 0, checked_out.stderr
    assert f"no loadout config at {root}" in checked_out.stderr
    (root / "README.md").write_text("Still before.\n")
    assert _git(root, "add", "README.md").returncode == 0
    committed = _git(root, "commit", "-qm", "old work")
    assert committed.returncode == 0, committed.stderr


def test_a_dotfiles_branch_without_the_source_folder_checks_out_and_commits(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "dotfiles"
    repo.mkdir()
    (repo / "zshrc").write_text("# shell\n")
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    _commit(repo, "before")
    assert _git(repo, "branch", "old").returncode == 0
    source = register(_global_source(repo / "loadout", "~/out.md"))
    _commit(repo, "adopt")
    _hooks(source)

    checked_out = _git(repo, "checkout", "-q", "old")

    assert checked_out.returncode == 0, checked_out.stderr
    assert f"no loadout config at {source}" in checked_out.stderr
    (repo / "zshrc").write_text("# shell, edited\n")
    assert _git(repo, "add", "zshrc").returncode == 0
    committed = _git(repo, "commit", "-qm", "old work")
    assert committed.returncode == 0, committed.stderr


def test_a_commit_deleting_a_tracked_source_is_still_checked(tmp_path: Path) -> None:
    """HEAD holds the config the commit removes, so there is something to check."""
    root = tmp_path / "project"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\n')
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _commit(root, "adopt")
    assert _git(root, "rm", "-rq", "loadout").returncode == 0

    assert (_nothing_to_protect("pre-commit", root), _nothing_to_protect("post-merge", root)) == (
        False,
        True,
    )
