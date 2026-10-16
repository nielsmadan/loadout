"""In a linked worktree of the registered source the post-* hooks do nothing and say so,
and `check --staged` still validates its snapshot (review 3, batch 2.2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.machine import registration
from loadout.worktrees import linked_worktree_source
from registration import register

pytestmark = pytest.mark.integration


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    (source / "instructions").mkdir(parents=True)
    (source / "instructions/x.md").write_text("Rules.\n")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'
    )
    register(source)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        _git(source, *args)
    worktree = tmp_path / "worktree"
    _git(source, "worktree", "add", "-q", str(worktree))
    return source, worktree


def test_a_post_checkout_hook_in_a_linked_worktree_does_nothing_and_says_so(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source, worktree = _worktree(tmp_path)

    assert (
        loadout.main(["hook-event", "post-checkout", "--root", str(worktree), "--", "a", "b", "1"])
        == 0
    )

    assert capsys.readouterr().err == (
        f"loadout: {worktree} is a linked worktree of the registered source {source}; "
        "nothing was synced here\n"
    )
    assert not (fake_home / "out.md").exists()


def test_check_staged_in_a_linked_worktree_validates_its_snapshot(tmp_path: Path, capsys) -> None:
    _, worktree = _worktree(tmp_path)
    (worktree / "instructions/x.md").write_text("Changed rules.\n")
    _git(worktree, "add", "-A")

    assert loadout.main(["check", "--staged", "--root", str(worktree)]) == 0

    assert capsys.readouterr().out == "staged loadout dependency snapshot is valid\n"


def test_a_folder_that_is_not_a_worktree_of_the_registered_source_is_still_refused(
    tmp_path: Path, capsys
) -> None:
    source, _ = _worktree(tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(source), str(clone)], check=True)

    assert loadout.main(["hook-event", "post-merge", "--root", str(clone)]) == 3

    assert "but this machine's registered source is" in capsys.readouterr().err


def test_the_registered_source_s_own_checkout_still_syncs_from_its_hook(
    tmp_path: Path, fake_home: Path
) -> None:
    source, _ = _worktree(tmp_path)

    assert loadout.main(["hook-event", "post-merge", "--root", str(source)]) == 0

    assert (fake_home / "out.md").read_text().endswith("Rules.\n")


def test_a_linked_worktree_of_another_repository_is_still_refused(tmp_path: Path, capsys) -> None:
    _worktree(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        _git(other, *args)
    elsewhere = tmp_path / "elsewhere"
    _git(other, "worktree", "add", "-q", str(elsewhere))

    assert loadout.main(["hook-event", "post-merge", "--root", str(elsewhere)]) == 3

    assert "but this machine's registered source is" in capsys.readouterr().err


def test_another_folder_of_the_source_s_own_worktree_is_not_a_linked_worktree(
    tmp_path: Path,
) -> None:
    """Review 4, batch 1.1: only a different worktree of the source's repository counts."""
    source, _ = _worktree(tmp_path)
    (source / "project").mkdir()

    assert linked_worktree_source(source / "project", registration()) is None


@pytest.mark.parametrize("command", ["check", "sync"])
def test_a_command_in_a_linked_worktree_says_to_run_it_at_the_source(
    tmp_path: Path, fake_home: Path, capsys, command: str
) -> None:
    """Review 5, batch 4.1: it advised `--registration replace`, which would move the
    registration to the worktree."""
    source, worktree = _worktree(tmp_path)
    capsys.readouterr()

    assert loadout.main([command, "--root", str(worktree)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {worktree} is a linked worktree of the registered source {source}; "
        f"run this from {source}\n"
    )


def test_git_hooks_install_works_from_a_linked_worktree(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The hooks folder is the source's too, and the hook finds its worktree when it runs."""
    source, worktree = _worktree(tmp_path)

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(worktree)]) == 0

    assert (source / ".git/hooks/pre-commit").is_file()
