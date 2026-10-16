"""Real git commands through installed hooks, in linked worktrees (review 4, batch 1.1).

Git exports `GIT_DIR` only to the hooks it runs, so a test that calls the hook command
in-process never sees it; these drive `git` itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.git_hooks import plan_hooks
from registration import register

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _loadout_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hooks run `loadout` from PATH, which must be this checkout's."""
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # Bounded: a hook that hangs fails the test rather than the run (review 5, batch 5.9).
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _committed(root: Path) -> Path:
    for args in (("init", "-q", "-b", "main"), ("add", "-A"), ("commit", "-qm", "base")):
        assert _git(root, *args).returncode == 0
    return root


def _global_source(folder: Path, destination: str) -> Path:
    (folder / "instructions").mkdir(parents=True)
    (folder / "instructions/x.md").write_text("Rules.\n")
    (folder / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        f'[instructions.mine]\ndestinations = ["{destination}"]\norder = ["x"]\n'
    )
    return folder


def _hooks(root: Path) -> None:
    assert (
        loadout.main(["integrate", "git-hooks", "install", "--root", str(root), "--regenerate"])
        == 0
    )


def test_an_unrelated_project_s_worktree_syncs_and_commits(tmp_path: Path) -> None:
    """Its git dir was read for the registered source's too, so the two looked like one
    repository: the hooks skipped the sync, and the commit skipped the gate."""
    register(_committed(_global_source(tmp_path / "source", "~/out.md")))
    project = tmp_path / "project"
    (project / "loadout/instructions").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (project / "loadout/permissions.toml").write_text("")
    (project / "loadout/instructions/main.md").write_text("Project rules.\n")
    _committed(project)
    _hooks(project)
    worktree = tmp_path / "project-wt"
    assert _git(project, "worktree", "add", "-q", str(worktree)).returncode == 0
    (worktree / "AGENTS.md").unlink()
    assert _git(worktree, "branch", "other").returncode == 0

    checked_out = _git(worktree, "checkout", "-q", "other")

    assert checked_out.returncode == 0, checked_out.stderr
    assert "Project rules." in (worktree / "AGENTS.md").read_text()
    # The registration gate still holds in the worktree's commits.
    _global_source(worktree, "~/.config/fish/conf.d/evil.fish")
    assert _git(worktree, "add", "loadout.toml", "instructions").returncode == 0
    committed = _git(worktree, "commit", "-qm", "pr")
    assert committed.returncode != 0
    assert "unstage it with `git rm --cached -- loadout.toml`" in committed.stderr


def test_a_worktree_of_the_default_layout_commits(tmp_path: Path) -> None:
    """With the source at `<repo>/loadout`, as `init global <dir>` makes it, the staged
    check looked for it at the root a hook's `GIT_DIR` gave, and every commit failed."""
    repo = tmp_path / "config"
    source = register(_global_source(repo / "loadout", "~/out.md"))
    _committed(repo)
    _hooks(source)
    worktree = tmp_path / "config-wt"
    assert _git(repo, "worktree", "add", "-q", str(worktree)).returncode == 0
    (worktree / "loadout/instructions/x.md").write_text("Changed.\n")
    assert _git(worktree, "add", "-A").returncode == 0

    committed = _git(worktree, "commit", "-qm", "change")

    assert committed.returncode == 0, committed.stderr


def test_a_registered_source_that_is_a_worktree_syncs_from_its_own_hooks(
    tmp_path: Path, fake_home: Path
) -> None:
    """It took itself for another worktree of itself and never synced, while its main
    worktree's hooks and commits were refused."""
    main = _committed(_global_source(tmp_path / "main", "~/out.md"))
    worktree = tmp_path / "wt"
    assert _git(main, "worktree", "add", "-q", "-b", "work", str(worktree)).returncode == 0
    register(worktree)
    # Installed after the worktree exists: the hooks folder is shared by both, and
    # install writes it once for them (review 4, ruling on batch 1).
    _hooks(worktree)
    assert _git(worktree, "branch", "other").returncode == 0

    checked_out = _git(worktree, "checkout", "-q", "other")

    assert checked_out.returncode == 0, checked_out.stderr
    assert (fake_home / "out.md").read_text().endswith("Rules.\n")
    (worktree / "instructions/x.md").write_text("More rules.\n")
    assert _git(worktree, "add", "-A").returncode == 0
    assert _git(worktree, "commit", "-qm", "more").returncode == 0
    merged = _git(main, "merge", "-q", "--no-edit", "other")
    assert merged.returncode == 0, merged.stderr
    assert f"{main} is a linked worktree of the registered source {worktree}" in merged.stderr
    (main / "instructions/x.md").write_text("Changed in main.\n")
    assert _git(main, "add", "-A").returncode == 0
    committed = _git(main, "commit", "-qm", "change")
    assert committed.returncode == 0, committed.stderr


@pytest.mark.parametrize("how", [["-a"], ["-o", "loadout.toml"]])
def test_a_commit_of_the_index_git_builds_is_the_one_checked(
    tmp_path: Path, how: list[str]
) -> None:
    """Review 5, batch 5.1: `commit -a` and `commit -o` hand the hook an index of their
    own in GIT_INDEX_FILE; reading the repository's index instead let a broken manifest in."""
    source = register(_committed(_global_source(tmp_path / "source", "~/out.md")))
    _hooks(source)
    (source / "loadout.toml").write_text("not [toml\n")

    committed = _git(source, "commit", "-qm", "broken", *how)

    assert committed.returncode != 0
    assert "loadout.toml" in committed.stderr


def _external_hook(project: Path, outside: Path, after: str, capsys) -> None:
    """Install into a hooks directory outside the repository, and paste the advice it
    prints into that directory's own pre-commit hook, `after` following it."""
    capsys.readouterr()
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(project)]) == 0
    out = capsys.readouterr().out
    advice = out[out.index("Git hook pre-commit") :].splitlines()[1:]
    hook = outside / "pre-commit"
    hook.write_text("#!/bin/sh\n" + "".join(f"{line[2:]}\n" for line in advice) + after)
    hook.chmod(0o755)


def _broken(root: Path) -> None:
    (root / "loadout/config.toml").write_text("harnesses = [\n")
    assert _git(root, "add", "-A").returncode == 0


@pytest.mark.parametrize("after", ["", "true\n"], ids=["advice last", "a command after it"])
def test_external_hook_advice_guards_by_repository_and_passes_a_refusal_on(
    tmp_path: Path, after: str, capsys
) -> None:
    """Review 6, batch 1.5: as the last line, the `[ … ] && …` guard failed every commit
    in every other repository; followed by another command, it let a refused commit
    land; and a linked worktree, whose top level differs, was never checked."""
    project = tmp_path / "project"
    (project / "loadout/instructions").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (project / "loadout/permissions.toml").write_text("")
    (project / "loadout/instructions/main.md").write_text("Project rules.\n")
    assert loadout.main(["sync", "--root", str(project)]) == 0
    _committed(project)
    outside = tmp_path / "global-hooks"
    outside.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.txt").write_text("x\n")
    _committed(other)
    for root in (project, other):
        assert _git(root, "config", "core.hooksPath", str(outside)).returncode == 0
    _external_hook(project, outside, after, capsys)
    worktree = tmp_path / "project-wt"
    assert _git(project, "worktree", "add", "-q", str(worktree)).returncode == 0
    (other / "x.txt").write_text("y\n")
    (project / "notes.txt").write_text("notes\n")
    for root in (other, project):
        assert _git(root, "add", "-A").returncode == 0
        committed = _git(root, "commit", "-qm", "change")
        assert committed.returncode == 0, committed.stderr

    _broken(project)
    _broken(worktree)

    refused = [_git(root, "commit", "-qm", "broken") for root in (project, worktree)]
    assert [result.returncode for result in refused] == [1, 1]
    assert [result.stderr.partition("\n")[0] for result in refused] == [
        "loadout: staged dependency snapshot is incomplete or invalid: "
        f"{root / 'loadout/config.toml'}: invalid TOML: Invalid value (at end of document)"
        for root in (project, worktree)
    ]
    assert [_git(root, "log", "-1", "--format=%s").stdout for root in (project, worktree)] == [
        "change\n",
        "base\n",
    ]


def test_advice_for_an_existing_hook_passes_a_refusal_on(tmp_path: Path, capsys) -> None:
    """Review 6, batch 1.5: pasted above the hook's own commands, a bare command let a
    refused commit land."""
    project = tmp_path / "project"
    (project / "loadout/instructions").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (project / "loadout/permissions.toml").write_text("")
    (project / "loadout/instructions/main.md").write_text("Project rules.\n")
    assert loadout.main(["sync", "--root", str(project)]) == 0
    _committed(project)
    hook = project / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\ntrue\n")
    hook.chmod(0o755)
    capsys.readouterr()
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(project)]) == 0
    out = capsys.readouterr().out
    advice = out[out.index("Preserved.") :].splitlines()[1:]
    hook.write_text("#!/bin/sh\n" + "".join(f"{line[2:]}\n" for line in advice) + "true\n")
    _broken(project)

    refused = _git(project, "commit", "-qm", "broken")

    assert refused.returncode == 1
    assert refused.stderr.startswith("loadout: staged dependency snapshot is incomplete")
    assert _git(project, "log", "-1", "--format=%s").stdout == "base\n"


def test_a_worktree_whose_folder_was_deleted_still_shares_the_hooks(tmp_path: Path) -> None:
    """Review 6, batch 3 (hp-wt-missing-dir): asking git about the folder failed."""
    project = tmp_path / "project"
    (project / "loadout").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["codex"]\n')
    _committed(project)
    worktree = tmp_path / "project-wt"
    assert _git(project, "worktree", "add", "-q", str(worktree)).returncode == 0
    shutil.rmtree(worktree)

    assert plan_hooks(project).placement == "shared"
