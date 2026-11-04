from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import git_runner
from loadout.git_privacy import still_tracked
from loadout.overlay_git import (
    branches_tracking,
    covered_by,
    excludes_file,
    scratch_repo,
    tracked_units,
    unignored,
)

pytestmark = pytest.mark.integration


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    return path.resolve()


def test_tracked_units_names_tracked_files_and_folders_like_still_tracked(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    (root / "dir").mkdir()
    (root / "dir" / "x.md").write_text("x\n")
    (root / "a.txt").write_text("a\n")
    _git(root, "add", "a.txt", "dir/x.md")
    (root / "u.txt").write_text("u\n")
    units = ["a.txt", "dir", "di", "u.txt"]

    tracked = tracked_units(root, units)

    assert tracked == frozenset({"a.txt", "dir"})
    assert tracked == frozenset(still_tracked(root, units))


def test_branches_tracking_reads_one_tree_per_branch_and_names_untracked_units_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "repo")
    (root / "README.md").write_text("readme\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-q", "-b", "other")
    (root / "opencode.json").write_text("{}\n")
    _git(root, "add", "opencode.json")
    _git(root, "commit", "-qm", "opencode")
    _git(root, "checkout", "-q", "main")
    calls: list[tuple[str, ...]] = []
    original = git_runner.run

    def spy(
        where: Path, *arguments: str, data: bytes | None = None, index: git_runner.Index = None
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        return original(where, *arguments, data=data, index=index)

    monkeypatch.setattr(git_runner, "run", spy)

    branches = branches_tracking(root, ["opencode.json", "README.md", "CLAUDE.local.md"])

    assert branches == {
        "opencode.json": ("other",),
        "README.md": ("main", "other"),
        "CLAUDE.local.md": (),
    }
    assert [call[0] for call in calls] == ["for-each-ref", "ls-tree", "ls-tree"]
    assert tracked_units(root, ["opencode.json", "README.md"]) == frozenset({"README.md"})


def test_branches_tracking_separates_a_unit_tracked_on_one_branch_from_another(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path / "repo")
    (root / "dir").mkdir()
    (root / "dir" / "x.md").write_text("x\n")
    (root / "*.json").write_text("{}\n")
    (root / "a.json").write_text("{}\n")
    _git(root, "add", "dir/x.md", ":(literal)*.json")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-q", "-b", "slim")
    _git(root, "rm", "-q", "-r", "dir")
    _git(root, "commit", "-qm", "slim")

    branches = branches_tracking(root, ["dir", "*.json", "a.json"])

    assert branches == {"dir": ("main",), "*.json": ("main", "slim"), "a.json": ()}


def test_covered_by_names_units_the_excludes_file_ignores_through_a_link(tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "ignore").write_text("**/CLAUDE.local.md\n")
    (tmp_path / "link").symlink_to(tmp_path / "real")

    with scratch_repo() as scratch:
        covered = covered_by(
            scratch,
            tmp_path / "link" / "ignore",
            ["CLAUDE.local.md", ".claude", "opencode.json"],
            frozenset({".claude"}),
        )

    assert covered == frozenset({"CLAUDE.local.md"})


def test_covered_by_reads_the_excludes_file_alone_not_the_targets_info_exclude(
    tmp_path: Path,
) -> None:
    root = _repo(tmp_path / "repo")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("**/CLAUDE.local.md\n")
    (tmp_path / "ignore").write_text("*.log\n")
    _git(root, "config", "core.excludesFile", str(tmp_path / "ignore"))

    with scratch_repo() as scratch:
        covered = covered_by(scratch, excludes_file(root), ["CLAUDE.local.md"], frozenset())

    assert covered == frozenset()
    assert unignored(root, ["CLAUDE.local.md", "notes.md"], frozenset()) == {"notes.md": ""}


def test_unignored_names_the_team_negation_that_re_includes_a_unit(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    (root / ".gitignore").write_text("*.json\n!opencode.json\n")

    result = unignored(
        root,
        ["opencode.json", "settings.json", "CLAUDE.local.md", ".claude"],
        frozenset({".claude"}),
    )

    assert result == {
        "opencode.json": ".gitignore:2:!opencode.json",
        "CLAUDE.local.md": "",
        ".claude": "",
    }


def test_excludes_file_reads_core_excludes_file_resolved(tmp_path: Path) -> None:
    root = _repo(tmp_path / "repo")
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    _git(root, "config", "core.excludesFile", str(tmp_path / "link" / "ignore"))

    assert excludes_file(root) == (tmp_path / "real" / "ignore").resolve()


def test_excludes_file_falls_back_to_xdg_then_home(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "repo")

    home_default = excludes_file(root)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    xdg_default = excludes_file(root)

    assert home_default == (fake_home / ".config" / "git" / "ignore").resolve()
    assert xdg_default == (tmp_path / "xdg" / "git" / "ignore").resolve()
