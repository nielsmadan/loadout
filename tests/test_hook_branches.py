"""The hooks let a branch with nothing of loadout's on it be checked out and committed to
(review 5, batch 2.4): one from before adoption, or one without the source folder."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import loadout
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


def _project(folder: Path) -> Path:
    (folder / "loadout/instructions").mkdir(parents=True)
    (folder / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (folder / "loadout/instructions/main.md").write_text("Rules.\n")
    return folder


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("change", [("rm", "-rq", "cfg"), ("mv", "cfg", "cfg2")], ids=["rm", "mv"])
def test_a_commit_deleting_or_renaming_the_source_folder_lands(
    tmp_path: Path, scope: str, change: tuple[str, ...]
) -> None:
    """Review 6, batch 1.6: the check ran git in the folder the commit removed, and
    refused with "cannot change to", so loadout could not be retired."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "zshrc").write_text("# shell\n")
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    source = repo / "cfg"
    if scope == "global":
        register(_global_source(source, "~/out.md"))
    else:
        _project(source)
    _commit(repo, "adopt")
    _hooks(source)
    assert _git(repo, *change).returncode == 0

    committed = _git(repo, "commit", "-qm", "retire")

    assert committed.returncode == 0, committed.stderr
    assert _git(repo, "log", "-1", "--format=%s").stdout == "retire\n"


@pytest.mark.parametrize(
    "name", ["Loadout.toml", "LOADOUT/Config.toml"], ids=["manifest", "project config"]
)
@pytest.mark.parametrize("where", ["staged", "committed"])
def test_a_case_variant_config_in_git_is_still_checked(
    tmp_path: Path, name: str, where: str
) -> None:
    """Review 6, batch 2.1: git was asked for the exact-case names, so a commit holding
    `Loadout.toml` found nothing to check."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Before.\n")
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _commit(root, "before")
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_text('[[source]]\nname = "s"\npath = "."\n')
    assert _git(root, "add", "-A").returncode == 0
    if where == "committed":
        assert _git(root, "commit", "-qm", "adopt").returncode == 0
        assert _git(root, "rm", "-q", "--cached", name).returncode == 0
    (root / name).unlink()

    assert _nothing_to_protect("pre-commit", root) is False


def test_the_nothing_to_do_note_names_the_check_for_a_commit_and_the_sync_otherwise(
    tmp_path: Path, capsys
) -> None:
    """Review 6, batch 3 (hk-nothing-work)."""
    root = tmp_path / "empty"
    root.mkdir()
    assert _git(root, "init", "-q", "-b", "main").returncode == 0

    codes = [
        loadout.main(["hook-event", event, "--root", str(root)])
        for event in ("pre-commit", "post-merge")
    ]

    assert (codes, capsys.readouterr().err) == (
        [0, 0],
        (
            f"loadout: no loadout config at {root} on this branch; nothing to check\n"
            f"loadout: no loadout config at {root} on this branch; nothing to sync\n"
        ),
    )


def test_a_config_only_in_the_commit_s_own_index_is_still_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 6, batch 3 (hk-cig-given): `commit -o` and `commit -a` hand the hook an
    index of their own, which is the one to ask."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Before.\n")
    assert _git(root, "init", "-q", "-b", "main").returncode == 0
    _commit(root, "before")
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    own = tmp_path / "own-index"
    env = {**os.environ, "GIT_INDEX_FILE": str(own)}
    for args in (("read-tree", "HEAD"), ("add", "loadout.toml")):
        subprocess.run(["git", "-C", str(root), *args], env=env, check=True)
    (root / "loadout.toml").unlink()
    monkeypatch.setenv("GIT_INDEX_FILE", str(own))

    assert _nothing_to_protect("pre-commit", root) is False
