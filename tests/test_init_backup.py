"""init protects an adoption with a before-image backup, not a journalled transaction (D-06).

A failure restores every path init touched, a killed init is offered for restore on the
next run, a completed one can be undone, and init commits nothing and stages nothing but
the untracking of the generated outputs it ignores.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout.migration_backup import backups, backups_root

pytestmark = pytest.mark.migration_integration

CLAUDE_SETTINGS = '{"permissions": {"deny": ["Bash(rm:*)"]}, "env": {"MODE": "x"}}\n'


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _project(root: Path, *, repository: bool = True) -> Path:
    _write(root / ".claude/settings.json", CLAUDE_SETTINGS)
    _write(root / "CLAUDE.md", "# Project\n\nBe careful.\n")
    _write(root / ".claude/skills/review/SKILL.md", "---\nname: review\ndescription: r\n---\nR.\n")
    if repository:
        _git(root, "init", "-q")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "base")
    return root


def _snapshot(root: Path, *, skip: tuple[Path, ...] = ()) -> dict[str, object]:
    result: dict[str, object] = {}
    for directory, directories, files in os.walk(root):
        here = Path(directory)
        directories[:] = [
            d
            for d in directories
            if here / d not in skip and not (here.name == ".git" and d in {"objects", "logs"})
        ]
        for name in (*directories, *files):
            path = here / name
            key = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode & 0o7777
            if path.is_symlink():
                result[key] = ("link", os.readlink(path))
            elif path.is_dir():
                result[key] = ("dir", mode)
            else:
                result[key] = (path.read_bytes(), mode)
    return result


def _home(home: Path) -> dict[str, object]:
    """HOME without the backup itself and the directories that hold it."""
    holders = {backups_root(), *backups_root().parents}
    return {
        key: value
        for key, value in _snapshot(home, skip=(backups_root(),)).items()
        if home / key not in holders
    }


def _init_project(root: Path, *extra: str) -> int:
    return loadout.main(["init", "project", "--root", str(root), "--harness", "claude", *extra])


def _fail_after_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail once init has applied every operation and written the first render."""
    deploy = workflow.deploy

    def failing(*args: object, **kwargs: object) -> object:
        deploy(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("injected after the first render")

    monkeypatch.setattr(workflow, "deploy", failing)


def test_a_failure_restores_every_path_including_the_index(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    home_before = _home(fake_home)
    _fail_after_deploy(monkeypatch)

    assert _init_project(root, "--yes") == 1

    assert "everything it changed was restored" in capsys.readouterr().err
    assert _snapshot(root) == before
    assert _home(fake_home) == home_before
    assert [b.interrupted for b in backups()] == [False]


def test_a_failure_restores_a_de_linked_symlink(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    _write(dotfiles / "settings.json", '{"permissions": {"allow": ["Bash(git status)"]}}\n')
    _write(dotfiles / "commands/foo.md", "Do foo.\n")
    (fake_home / ".claude").symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    dotfiles_before = _snapshot(dotfiles)
    home_before = _home(fake_home)
    _fail_after_deploy(monkeypatch)

    argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 1

    assert (fake_home / ".claude").readlink() == dotfiles
    assert _snapshot(dotfiles) == dotfiles_before
    assert _home(fake_home) == home_before
    assert sorted(p.name for p in source.iterdir()) == []


def test_a_killed_init_is_offered_for_restore_on_the_next_run(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    with pytest.MonkeyPatch.context() as patch:
        _fail_after_deploy(patch)
        # A kill never reaches the restore, so the marker stays behind.
        patch.setattr(workflow, "_failed", lambda *args, **kwargs: 1)
        assert _init_project(root, "--yes") == 1
    capsys.readouterr()

    assert _init_project(root, "--yes", "--json") == 2

    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "interrupted"
    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "restored"
    assert _snapshot(root) == before
    assert [b.interrupted for b in backups()] == [False]


def test_a_project_init_makes_no_commit_and_stages_only_the_untracking(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    head = _git(root, "rev-parse", "HEAD").stdout

    assert _init_project(root, "--yes") == 0

    assert _git(root, "rev-parse", "HEAD").stdout == head
    assert _git(root, "diff", "--cached", "--name-status").stdout.splitlines() == [
        "D\t.claude/settings.json",
        "D\t.claude/skills/review/SKILL.md",
        "D\tCLAUDE.md",
    ]
    assert "Bash(rm:*)" in (root / ".claude/settings.json").read_text()


def test_a_project_without_a_repository_gets_none(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", repository=False)

    assert _init_project(root, "--yes") == 0

    assert not (root / ".git").exists()
    assert (root / "loadout/config.toml").is_file()


def test_a_completed_init_can_be_undone_and_keeps_a_later_edit(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    assert _init_project(root, "--yes") == 0
    edited = root / "loadout/instructions/main.md"
    edited.write_text("# Edited after init\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["kept"] == [str(root / "loadout"), str(edited.parent), str(edited)]
    assert edited.read_text() == "# Edited after init\n"
    after = _snapshot(root)
    assert {key: value for key, value in after.items() if not key.startswith("loadout")} == {
        key: value for key, value in before.items() if not key.startswith("loadout")
    }


def test_a_global_source_gets_a_repository_and_no_commit(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0

    assert (source / ".git").is_dir()
    assert _git(source, "rev-parse", "--verify", "HEAD", check=False).returncode != 0
    assert "Nothing was committed" in capsys.readouterr().out


def test_only_the_latest_completed_backup_is_kept(tmp_path: Path, capsys) -> None:
    first = _project(tmp_path / "a")
    second = _project(tmp_path / "b")

    assert _init_project(first, "--yes") == 0
    assert _init_project(second, "--yes") == 0

    (kept,) = backups()
    assert json.loads((kept.directory / "manifest.json").read_text())["root"] == str(second)


def test_undoing_a_de_linked_adoption_puts_the_link_back(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    _write(dotfiles / "commands/foo.md", "Do foo.\n")
    (fake_home / ".claude").symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    dotfiles_before = _snapshot(dotfiles)
    argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0
    assert not (fake_home / ".claude").is_symlink()
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert (fake_home / ".claude").readlink() == dotfiles
    assert _snapshot(dotfiles) == dotfiles_before


def test_undoing_keeps_a_source_repository_the_user_committed_to(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    _git(source, "add", "-A")
    _git(source, "commit", "-qm", "adopt")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(source / ".git") in json.loads(capsys.readouterr().out)["kept"]
    assert _git(source, "log", "--format=%s").stdout == "adopt\n"
    assert (fake_home / ".claude/CLAUDE.md").read_text() == "# Me\n"
