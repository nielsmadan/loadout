"""init protects an adoption with a before-image backup, not a journalled transaction (ADR 0028).

A failure restores every path init touched, a killed init is offered for restore on the
next run, a completed one can be undone, and init commits nothing and stages nothing but
the untracking of the generated outputs it ignores.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout import migration_backup, migration_git
from loadout.errors import LoadoutError
from loadout.migration_backup import backups, backups_root
from loadout.migration_images import Image
from loadout.written import record_written, text_entry, written_state_path

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

    assert "what it changed was restored" in capsys.readouterr().err
    assert _snapshot(root) == before
    assert _home(fake_home) == home_before
    assert backups() == ()


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
    assert backups() == ()


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


def test_restore_through_a_written_through_config_keeps_the_dotfiles_file(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """~/.config linked into a dotfiles repo is written through (ADR 0023); undoing the
    adoption must put the dotfiles copy back, not delete it (review 1.1)."""
    config = tmp_path / "dotfiles/config"
    agents = _write(config / "opencode/AGENTS.md", "# oc\n")
    (fake_home / ".config").symlink_to(config, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    argv = ["init", "global", str(source), "--harness", "opencode", "--yes"]
    assert loadout.main(argv) == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert agents.read_text() == "# oc\n"
    assert (fake_home / ".config").readlink() == config


def test_a_failure_through_a_written_through_config_restores_and_leaves_no_marker(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    config = tmp_path / "dotfiles/config"
    agents = _write(config / "opencode/AGENTS.md", "# oc\n")
    (fake_home / ".config").symlink_to(config, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    _fail_after_deploy(monkeypatch)

    assert loadout.main(["init", "global", str(source), "--harness", "opencode", "--yes"]) == 1

    assert agents.read_text() == "# oc\n"
    assert backups() == ()


def test_a_restored_backup_is_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A failed init restores itself; its backup must not be replayed over later work,
    and the earlier completed adoption stays the one to undo (review 1.2)."""
    first = _project(tmp_path / "a")
    second = _project(tmp_path / "b")
    first_before = _snapshot(first)
    assert _init_project(first, "--yes") == 0
    with pytest.MonkeyPatch.context() as patch:
        _fail_after_deploy(patch)
        assert _init_project(second, "--yes") == 1
    mine = _write(second / "loadout/mine.md", "mine\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert mine.read_text() == "mine\n"
    assert _snapshot(first) == first_before
    with pytest.raises(LoadoutError, match="no init backup to restore"):
        workflow.run_restore(yes=True, as_json=True)


def test_undoing_a_de_linked_adoption_leaves_it_whole_when_the_harness_wrote_since(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Restoring the link means emptying the directory; with a file the harness wrote in
    it, nothing under the harness root is touched and the root is reported (review 1.3)."""
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    _write(dotfiles / "commands/foo.md", "Do foo.\n")
    claude = fake_home / ".claude"
    claude.symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    history = _write(claude / "history.jsonl", "{}\n")
    adopted = _snapshot(claude)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    kept = json.loads(capsys.readouterr().out)["kept"]
    assert str(claude) in kept and str(history) in kept
    assert _snapshot(claude) == adopted
    assert (claude / "commands").readlink() == dotfiles / "commands"


def test_an_interrupted_restore_keeps_what_the_harness_wrote_into_a_new_directory(
    tmp_path: Path, capsys
) -> None:
    """A directory a killed init created can gain the harness's own files before the
    restore; those survive, and so does the directory (review 1.4)."""
    root = _write(tmp_path / "p/CLAUDE.md", "# Project\n").parent
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    with pytest.MonkeyPatch.context() as patch:
        _fail_after_deploy(patch)
        patch.setattr(workflow, "_failed", lambda *args, **kwargs: 1)
        assert _init_project(root, "--yes") == 1
    local = _write(root / ".claude/settings.local.json", '{"model": "mine"}\n')
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert local.read_text() == '{"model": "mine"}\n'
    assert not (root / ".claude/settings.json").exists()
    assert json.loads(capsys.readouterr().out)["kept"] == [str(root / ".claude")]


def test_a_backup_that_cannot_be_written_leaves_nothing_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 2.1: a backup that fails half way must not block every later init."""
    root = _project(tmp_path / "p")
    before = _snapshot(root)

    def unreadable(path: Path) -> object:
        raise PermissionError(f"cannot read {path}")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("loadout.migration_backup.snapshot", unreadable)
        assert _init_project(root, "--yes") == 3

    assert "cannot read" in capsys.readouterr().err
    assert backups() == ()
    assert _snapshot(root) == before
    assert _init_project(root, "--yes") == 0


def _de_linked(tmp_path: Path, home: Path) -> tuple[Path, Path, Path]:
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    _write(dotfiles / "commands/foo.md", "Do foo.\n")
    claude = home / ".claude"
    claude.symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    return dotfiles, claude, source


def test_a_link_re_pointed_since_init_is_kept(tmp_path: Path, fake_home: Path, capsys) -> None:
    """Review E: a link the user re-pointed is theirs, not init's de-link to undo."""
    _, claude, _ = _de_linked(tmp_path, fake_home)
    newdots = tmp_path / "newdots"
    newdots.mkdir()
    shutil.rmtree(claude)
    claude.symlink_to(newdots, target_is_directory=True)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(claude) in json.loads(capsys.readouterr().out)["kept"]
    assert claude.readlink() == newdots


def test_a_project_moved_behind_a_link_is_kept_whole(tmp_path: Path, capsys) -> None:
    """Review F: nothing is removed when a path can no longer be reached directly."""
    root = _project(tmp_path / "real/p")
    assert _init_project(root, "--yes") == 0
    (tmp_path / "real").rename(tmp_path / "moved")
    (tmp_path / "real").symlink_to(tmp_path / "moved", target_is_directory=True)
    moved = _snapshot(tmp_path / "moved/p")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    reasons = json.loads(capsys.readouterr().out)["reasons"]
    behind = {path: why for path, why in reasons.items() if Path(path).is_relative_to(root)}
    assert sorted(behind) == [
        str(root / name)
        for name in (
            ".claude/settings.json",
            ".git/index",
            ".gitignore",
            "CLAUDE.md",
            "loadout",
            "loadout/config.toml",
            "loadout/instructions",
            "loadout/instructions/main.md",
            "loadout/permissions.toml",
            "loadout/settings",
            "loadout/settings/claude.json",
            "loadout/skills",
            "loadout/skills/review",
            "loadout/skills/review/SKILL.md",
        )
    ]
    assert set(behind.values()) == {"now reached through a link added since init"}
    assert _snapshot(tmp_path / "moved/p") == moved
    assert len(backups()) == 1


def test_an_older_backup_is_refused_with_the_directory_to_delete(tmp_path: Path) -> None:
    """Review G."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()
    manifest = backup.directory / "manifest.json"
    document = json.loads(manifest.read_text())
    manifest.write_text(json.dumps({**document, "version": 1, "after": None}))

    with pytest.raises(LoadoutError, match=f"{backup.directory}.*delete that directory"):
        workflow.run_restore(yes=True, as_json=True)


def _killed(patch: pytest.MonkeyPatch) -> None:
    patch.setattr(workflow, "_failed", lambda *args, **kwargs: 1)


def test_a_run_killed_after_untracking_restores_the_index(tmp_path: Path, capsys) -> None:
    """Review H: the untracking is declared before Git runs, so it is known as init's."""
    root = _project(tmp_path / "p")
    untrack = migration_git.untrack

    def untrack_then_die(*args: object) -> None:
        untrack(*args)  # type: ignore[arg-type]
        raise RuntimeError("killed after untracking")

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "untrack", untrack_then_die)
        assert _init_project(root, "--yes") == 1
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert _git(root, "diff", "--cached", "--name-only").stdout == ""


def test_a_run_killed_after_untracking_keeps_what_the_user_staged_since(
    tmp_path: Path, capsys
) -> None:
    """Review 5, batch 5.3: an index the user changed after the kill is kept whole rather
    than reset, so what they staged stays staged."""
    root = _project(tmp_path / "p")
    untrack = migration_git.untrack

    def untrack_then_die(*args: object) -> None:
        untrack(*args)  # type: ignore[arg-type]
        raise RuntimeError("killed after untracking")

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "untrack", untrack_then_die)
        assert _init_project(root, "--yes") == 1
    _write(root / "notes.md", "mine\n")
    _git(root, "add", "notes.md")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    # The index is someone else's again, so it stays as the user left it, and says why.
    index = root / ".git/index"
    assert json.loads(capsys.readouterr().out)["reasons"] == {str(index): "changed since init"}
    assert "A  notes.md" in _git(root, "status", "--short").stdout.splitlines()


def test_a_run_killed_after_git_init_removes_the_new_repository(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """What `git init` writes is recorded before it runs, so a kill straight after it
    still knows the files as init's (review 2, G7)."""
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()
    git = migration_git.git

    def git_then_die(root: Path, *args: str, **kwargs: object) -> object:
        result = git(root, *args, **kwargs)  # type: ignore[arg-type]
        if args[:1] == ("init",):
            raise RuntimeError("killed after git init")
        return result

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "git", git_then_die)
        argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
        assert loadout.main(argv) == 1
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert not (source / ".git").exists()


def test_a_project_with_only_claude_md_ignores_it_once(tmp_path: Path, capsys) -> None:
    """Review I."""
    root = _write(tmp_path / "p/CLAUDE.md", "# Project\n").parent
    _git(root, "init", "-q")

    assert _init_project(root, "--yes") == 0

    assert (root / ".gitignore").read_text().splitlines().count("/CLAUDE.md") == 1


def test_a_kept_link_can_be_restored_once_its_directory_is_init_s_again(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review J: the backup keeps what it could not restore, and names the link target."""
    dotfiles, claude, _ = _de_linked(tmp_path, fake_home)
    history = _write(claude / "history.jsonl", "{}\n")
    capsys.readouterr()
    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["links"] == {str(claude): str(dotfiles)}
    history.unlink()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert claude.readlink() == dotfiles
    assert backups() == ()


def test_restore_keeps_the_record_of_a_file_written_since_init(tmp_path: Path, capsys) -> None:
    """Review K: the write record loses only what restore reverted."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    _write(root / "loadout/skills/added/SKILL.md", "---\nname: added\ndescription: a\n---\nA.\n")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    written = json.loads(written_state_path(root).read_text())["written"]
    assert str(root / ".claude/skills/added/SKILL.md") in written
    assert str(root / ".claude/settings.json") not in written


def test_a_marker_without_a_manifest_does_not_block_init(tmp_path: Path, capsys) -> None:
    """Review L."""
    stray = backups_root() / "20260101T000000000000Z"
    stray.mkdir(parents=True)
    (stray / "in-progress").write_text("")
    root = _project(tmp_path / "p")

    assert _init_project(root, "--yes") == 0


def test_every_init_json_answer_carries_status_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 2.11: one envelope on every path, including restore with no backup."""
    root = _project(tmp_path / "p")
    answers = []
    for argv, code in (
        (["init", "project", "--root", str(root), "--harness", "claude", "--dry-run", "--json"], 0),
        (["init", "project", "--root", str(root), "--harness", "claude", "--yes", "--json"], 0),
        (["init", "restore", "--yes", "--json"], 0),
        (["init", "restore", "--yes", "--json"], 3),
    ):
        assert loadout.main(argv) == code
        envelope = json.loads(capsys.readouterr().out)
        answers.append((envelope["status"], envelope["exit_code"]))

    assert answers == [("preview", 0), ("complete", 0), ("restored", 0), ("error", 3)]


def _settings_entry(manifest: dict) -> dict:
    (entry,) = (
        e for e in manifest["entries"] if Path(e["path"]).parts[-2:] == (".claude", "settings.json")
    )
    return entry


def test_each_before_image_is_its_own_file_beside_the_manifest(tmp_path: Path) -> None:
    """Review 2.8: the manifest names each image by digest instead of carrying it."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()

    entry = _settings_entry(json.loads((backup.directory / "manifest.json").read_text()))

    digest = hashlib.sha256(CLAUDE_SETTINGS.encode()).hexdigest()
    assert entry["sha256"] == digest
    assert (backup.directory / "images" / digest).read_text() == CLAUDE_SETTINGS


def test_a_damaged_image_refuses_the_restore_and_changes_nothing(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()
    entry = _settings_entry(json.loads((backup.directory / "manifest.json").read_text()))
    image = backup.directory / "images" / entry["sha256"]
    image.write_text("{}\n")
    adopted = _snapshot(root)

    with pytest.raises(LoadoutError, match=f"{image}.*damaged"):
        workflow.run_restore(yes=True, as_json=True)

    assert _snapshot(root) == adopted
    assert image.read_text() == "{}\n"


def test_a_reduced_backup_keeps_only_the_images_of_what_it_kept(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (root / ".claude/settings.json").write_text('{"mine": true}\n')
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    (backup,) = backups()
    manifest = json.loads((backup.directory / "manifest.json").read_text())
    assert (
        _settings_entry(manifest)["sha256"] == hashlib.sha256(CLAUDE_SETTINGS.encode()).hexdigest()
    )
    stored = {image.name for image in (backup.directory / "images").iterdir()}
    assert stored == {e["sha256"] for e in manifest["entries"] if e["kind"] == "file"}


def test_the_preview_lists_only_operations_init_applies(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3.1: the first render's outputs are listed under `outputs`, not as operations."""
    settings = _write(fake_home / ".claude/settings.json", CLAUDE_SETTINGS)
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()
    argv = ["init", "global", str(source), "--harness", "claude", "--dry-run", "--json"]

    assert loadout.main(argv) == 0

    preview = json.loads(capsys.readouterr().out)
    assert {o["phase"] for o in preview["operations"]} == {"source", "registration", "ignore"}
    assert str(settings) in {o["path"] for o in preview["outputs"]}


def test_the_preview_carries_exactly_these_fields(tmp_path: Path, fake_home: Path, capsys) -> None:
    """Review 3.5: a field the plan never fills is not part of `init --json`."""
    _write(fake_home / ".claude/settings.json", CLAUDE_SETTINGS)
    source = tmp_path / "source"
    source.mkdir()
    argv = ["init", "global", str(source), "--harness", "claude", "--dry-run", "--json"]

    assert loadout.main(argv) == 0

    assert sorted(json.loads(capsys.readouterr().out)) == [
        "already_initialized",
        "candidates",
        "categories",
        "complete",
        "delinked",
        "exit_code",
        "expected_outputs",
        "generated_writes",
        "harnesses",
        "ignores",
        "initialized",
        "issues",
        "mappings",
        "notes",
        "obsolete",
        "operations",
        "originals",
        "outputs",
        "private_paths",
        "registration",
        "root",
        "scope",
        "secrets",
        "source_repository",
        "source_root",
        "source_writes",
        "status",
        "unadopted",
        "untrack_paths",
        "validated",
    ]


def test_a_first_render_that_is_not_written_restores_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 5.1: the render refusing is a failure like any other, so init undoes itself."""
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    monkeypatch.setattr(workflow, "_first_sync", lambda *args, **kwargs: None)

    assert _init_project(root, "--yes") == 1

    assert "the first render was not written" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_an_interrupt_during_apply_restores_every_path_and_still_interrupts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5.2: Ctrl-C is not an Exception, and must neither skip the restore nor be
    swallowed by it."""
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    deploy = workflow.deploy

    def interrupted(*args: object, **kwargs: object) -> object:
        deploy(*args, **kwargs)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(workflow, "deploy", interrupted)

    with pytest.raises(KeyboardInterrupt):
        _init_project(root, "--yes")

    assert _snapshot(root) == before


def test_the_backup_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    """Review 5.3: before-images can hold anything the adopted files held."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()

    modes = {
        "root": backups_root().stat().st_mode & 0o777,
        "backup": backup.directory.stat().st_mode & 0o777,
        "images": (backup.directory / "images").stat().st_mode & 0o777,
        "manifest": (backup.directory / "manifest.json").stat().st_mode & 0o777,
    }
    image_modes = {p.stat().st_mode & 0o777 for p in (backup.directory / "images").iterdir()}

    assert modes == {"root": 0o700, "backup": 0o700, "images": 0o700, "manifest": 0o600}
    assert image_modes == {0o600}


def _interrupted_init(root: Path) -> None:
    """An init killed after its first render, leaving its backup marked in progress."""
    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        _fail_after_deploy(patch)
        assert _init_project(root, "--yes") == 1


def _terminal(monkeypatch: pytest.MonkeyPatch, answer: str) -> list[str]:
    prompts: list[str] = []

    def reply(prompt: str) -> str:
        prompts.append(prompt)
        return answer

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(workflow, "_answer", reply)
    return prompts


def test_the_next_init_at_a_terminal_restores_an_interrupted_one_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 5.9."""
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    _interrupted_init(root)
    prompts = _terminal(monkeypatch, "y")

    assert _init_project(root) == 0

    assert prompts[0].startswith("An earlier init did not finish.")
    assert "Restored what init changed" in capsys.readouterr().out
    assert _snapshot(root) == before


def test_declining_to_restore_an_interrupted_init_keeps_its_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _interrupted_init(root)
    _terminal(monkeypatch, "n")

    assert _init_project(root) == 2

    assert "declined; the backup stays" in capsys.readouterr().out
    assert [b.interrupted for b in backups()] == [True]


def test_without_a_terminal_an_interrupted_init_is_named_and_left(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    _interrupted_init(root)
    (backup,) = backups()
    capsys.readouterr()

    assert _init_project(root, "--yes") == 2

    assert (
        f"an earlier init did not finish; restore it with `loadout init restore` (backup {backup.directory})"
        in capsys.readouterr().err
    )
    assert backup.interrupted


def test_init_restore_without_yes_or_a_terminal_is_refused(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore"]) == 2

    assert "init restore requires --yes" in capsys.readouterr().err
    assert len(backups()) == 1


def test_init_restore_at_a_terminal_asks_and_honours_a_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    adopted = _snapshot(root)
    prompts = _terminal(monkeypatch, "n")
    capsys.readouterr()

    assert loadout.main(["init", "restore"]) == 0

    assert prompts[0].startswith("Restore everything init changed")
    assert "declined; no changes made" in capsys.readouterr().out
    assert _snapshot(root) == adopted


def test_init_restore_at_a_terminal_restores_on_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    assert _init_project(root, "--yes") == 0
    _terminal(monkeypatch, "yes")
    capsys.readouterr()

    assert loadout.main(["init", "restore"]) == 0

    assert "Restored what init changed" in capsys.readouterr().out
    assert _snapshot(root) == before


def _global_source(source: Path, fake_home: Path) -> Path:
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source.mkdir(parents=True)
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    return source


def test_undoing_removes_a_new_source_repository_left_as_init_made_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_source(tmp_path / "source", fake_home)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert not (source / ".git").exists()


def test_undoing_keeps_a_new_source_repository_given_a_hook(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 1.6: no commit and no index was the whole test, so a hook went too."""
    source = _global_source(tmp_path / "source", fake_home)
    hook = _write(source / ".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert {str(source / ".git"), str(hook)} <= set(json.loads(capsys.readouterr().out)["kept"])
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"


def test_undoing_keeps_a_new_source_repository_given_a_remote(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_source(tmp_path / "source", fake_home)
    _git(source, "remote", "add", "origin", "https://example.invalid/me/agents.git")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(source / ".git") in json.loads(capsys.readouterr().out)["kept"]
    assert _git(source, "remote").stdout == "origin\n"


def test_undoing_keeps_a_new_source_repository_moved_behind_a_link(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_source(tmp_path / "real/source", fake_home)
    (tmp_path / "real").rename(tmp_path / "moved")
    (tmp_path / "real").symlink_to(tmp_path / "moved", target_is_directory=True)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(source / ".git") in json.loads(capsys.readouterr().out)["kept"]
    assert (tmp_path / "moved/source/.git/HEAD").is_file()


def _another_synced_project(root: Path) -> None:
    """Its write record means the record directory exists before init, so undoing init
    keeps none of that directory, and only the record's own image can carry it."""
    _write(root / "loadout/config.toml", 'harnesses = ["claude"]\n')
    _write(root / "loadout/permissions.toml", '[shell]\ndeny = ["rm"]\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0


def _undo_kept_back_an_edit(root: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    """Init, edit an adopted output, undo: the edit is kept and the backup reduced."""
    _another_synced_project(root.parent / "other")
    assert _init_project(root, "--yes") == 0
    settings = root / ".claude/settings.json"
    left = settings.read_text()
    settings.write_text('{"mine": true}\n')
    capsys.readouterr()
    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1
    assert str(settings) in json.loads(capsys.readouterr().out)["kept"]
    return settings, left


def test_a_reduced_backup_finishes_the_undo_once_the_edit_is_resolved(
    tmp_path: Path, capsys
) -> None:
    """Review 2, 1.7: the reduced backup dropped the write record's image, so the second
    restore raised KeyError after it had already put files back."""
    root = _project(tmp_path / "p")
    settings, left = _undo_kept_back_an_edit(root, capsys)
    settings.write_text(left)

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert settings.read_text() == CLAUDE_SETTINGS
    assert backups() == ()


def test_a_restore_killed_while_reducing_the_backup_can_still_finish(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Killed after the reduced manifest is written and before the spare images go."""
    root = _project(tmp_path / "p")
    _another_synced_project(tmp_path / "other")
    assert _init_project(root, "--yes") == 0
    settings = root / ".claude/settings.json"
    left = settings.read_text()
    settings.write_text('{"mine": true}\n')
    write = migration_backup._write_manifest
    calls: list[object] = []

    def write_then_die(*args: object) -> None:
        write(*args)  # type: ignore[arg-type]
        calls.append(args)
        if len(calls) == 1:
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(migration_backup, "_write_manifest", write_then_die)
        with pytest.raises(KeyboardInterrupt):
            workflow.run_restore(yes=True, as_json=True)
    settings.write_text(left)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert settings.read_text() == CLAUDE_SETTINGS


def test_a_backup_missing_a_record_image_is_refused_before_anything_changes(
    tmp_path: Path, capsys
) -> None:
    """A backup reduced before review 2, 1.7 dropped the record's image."""
    root = _project(tmp_path / "p")
    settings, left = _undo_kept_back_an_edit(root, capsys)
    (backup,) = backups()
    manifest = backup.directory / "manifest.json"
    document = json.loads(manifest.read_text())
    records = set(document["records"])
    document["entries"] = [e for e in document["entries"] if e["path"] not in records]
    manifest.write_text(json.dumps(document))
    settings.write_text(left)

    with pytest.raises(LoadoutError, match=r"write record .* has no before-image"):
        workflow.run_restore(yes=True, as_json=True)

    assert settings.read_text() == left


def test_a_shared_document_linked_file_by_file_is_adopted(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 2.3: a Stow-style link to one co-owned file started absent, so the
    first render dropped the keys loadout does not own and init failed its own check."""
    dotfiles = tmp_path / "dotfiles"
    config = _write(dotfiles / "config.toml", 'model = "gpt-5"\n')
    (fake_home / ".codex").mkdir()
    (fake_home / ".codex/config.toml").symlink_to(config)
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 0

    written = fake_home / ".codex/config.toml"
    assert not written.is_symlink()
    assert 'model = "gpt-5"' in written.read_text()
    assert config.read_text() == 'model = "gpt-5"\n'
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert written.readlink() == config
    assert config.read_text() == 'model = "gpt-5"\n'


def test_a_torn_write_record_does_not_stop_a_restore(tmp_path: Path, capsys) -> None:
    """Review 2, 2.5: the record was parsed strictly, so every restore exited 4."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    record = written_state_path(root)
    record.write_text('{"version": 1, "written": {')
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    # Not provably init's any more, so the record and the folder holding it stay.
    assert str(record.parent) in json.loads(capsys.readouterr().out)["kept"]
    assert record.read_text() == '{"version": 1, "written": {'
    assert (root / ".claude/settings.json").read_text() == CLAUDE_SETTINGS


def test_restore_skips_a_newer_backup_directory_without_a_manifest(tmp_path: Path, capsys) -> None:
    """A run killed before its manifest was written left a directory that hid the real
    backup behind it."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()
    (backup.directory.parent / "99991231T000000000000Z").mkdir()
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert (root / ".claude/settings.json").read_text() == CLAUDE_SETTINGS


def test_a_backup_killed_before_it_applied_anything_is_passed_over(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Killed between its manifest and its marker, a backup of a run that never applied
    looked complete, so restoring it did nothing and hid the real one."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    other = _project(tmp_path / "o")
    create = migration_backup.create_backup

    def create_then_die(*args: object, **kwargs: object) -> object:
        backup = create(*args, **kwargs)  # type: ignore[arg-type]
        (backup.directory / migration_backup.MARKER).unlink()
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "create_backup", create_then_die)
        with pytest.raises(KeyboardInterrupt):
            _init_project(other, "--yes")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert (root / ".claude/settings.json").read_text() == CLAUDE_SETTINGS


def test_a_killed_restore_is_offered_again(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restore of a completed backup set no in-progress marker, so the next init's
    `complete()` deleted the half-spent backup."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0

    def die(*args: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(migration_backup, "_put_back", die)
        with pytest.raises(KeyboardInterrupt):
            workflow.run_restore(yes=True, as_json=True)
    capsys.readouterr()

    assert loadout.main(["init", "project", "--root", str(root), "--json", "--dry-run"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "interrupted"
    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0
    assert (root / ".claude/settings.json").read_text() == CLAUDE_SETTINGS


def _git_runtime_files(git_dir: Path) -> None:
    """What read-only git leaves in a repository with no commit: fsmonitor's daemon
    state, a fetch, gc's packing and a lock."""
    (git_dir / "fsmonitor--daemon/cookies").mkdir(parents=True)
    _write(git_dir / "fsmonitor--daemon.ipc", "")
    _write(git_dir / "FETCH_HEAD", "")
    _write(git_dir / "packed-refs", "# pack-refs with: peeled fully-peeled sorted\n")
    _write(git_dir / "info/refs", "")
    _write(git_dir / "objects/info/packs", "\n")
    _write(git_dir / "config.lock", "")


def test_undoing_removes_a_new_repository_git_only_read_from(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, G6: a `git status` with fsmonitor, a fetch or gc made a clean undo
    impossible."""
    source = _global_source(tmp_path / "source", fake_home)
    _git_runtime_files(source / ".git")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert not (source / ".git").exists()


def test_undoing_keeps_a_new_repository_whose_packed_refs_name_a_ref(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Git's own files count as runtime only while the repository has no ref."""
    source = _global_source(tmp_path / "source", fake_home)
    _git_runtime_files(source / ".git")
    _write(source / ".git/packed-refs", f"{'1' * 40} refs/heads/main\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(source / ".git") in json.loads(capsys.readouterr().out)["kept"]


def test_the_preview_names_a_file_that_only_gains_loadout_s_banner(tmp_path: Path, capsys) -> None:
    """Review 2, 2.13: the preview said it reproduced the setup exactly while the first
    render added a banner to the file."""
    root = _project(tmp_path / "p")

    assert _init_project(root, "--dry-run", "--json") == 0
    changes = {o["path"]: o["change"] for o in json.loads(capsys.readouterr().out)["outputs"]}
    assert _init_project(root, "--dry-run") == 0

    assert changes[str(root / "CLAUDE.md")] == "normalised"
    assert f"Normalise: {root / 'CLAUDE.md'}" in capsys.readouterr().out


def test_init_json_lists_the_files_its_first_render_wrote(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")

    assert _init_project(root, "--yes", "--json") == 0

    result = json.loads(capsys.readouterr().out)
    assert str(root / "CLAUDE.md") in result["rendered"]
    assert str(root / ".claude/settings.json") in result["rendered"]


def test_restore_gives_each_kept_path_its_reason(tmp_path: Path, capsys) -> None:
    """Review 2, 2.13: one label covered every kept path, whatever kept it."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    settings = root / ".claude/settings.json"
    settings.write_text('{"mine": true}\n')
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["reasons"][str(settings)] == "changed since init"


def test_restore_names_a_replaced_link_once(tmp_path: Path, fake_home: Path, capsys) -> None:
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    claude = fake_home / ".claude"
    claude.symlink_to(dotfiles, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0
    _write(claude / "history.jsonl", "{}\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes"]) == 1

    lines = capsys.readouterr().out.splitlines()
    named = [line for line in lines if line.startswith(f"{claude} ") or line.endswith(str(claude))]
    assert named == [f"{claude} used to link to {dotfiles}; it stays a directory for now."]


def test_a_restore_that_puts_nothing_back_says_so(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    _undo_kept_back_an_edit(root, capsys)

    assert loadout.main(["init", "restore", "--yes"]) == 1

    out = capsys.readouterr().out
    assert out.startswith("Nothing could be restored.")


def test_a_spent_backup_is_reported_as_gone(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["backup"] is None


def test_a_write_record_that_predates_init_survives_its_undo(tmp_path: Path, capsys) -> None:
    """Review 2, batch 5 1.2: restore deleted a record emptied of init's entries even when
    it existed before init, as if init had created it."""
    root = _project(tmp_path / "p")
    record_written(root, {root / "CLAUDE.md": text_entry(root / "CLAUDE.md", "earlier\n")})
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(written_state_path(root).read_text())["written"] == {}


def test_a_restore_that_keeps_something_leaves_its_backup_finished(tmp_path: Path, capsys) -> None:
    """Review 2, batch 5 1.3: restore marks the backup in progress before it starts, so a
    reduced backup that kept that marker blocked every later init as interrupted."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (root / "CLAUDE.md").write_text("edited after init\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    (backup,) = backups()
    assert backup.interrupted is False
    assert str(root / "CLAUDE.md") in json.loads(capsys.readouterr().out)["kept"]


def test_a_run_killed_after_git_init_keeps_work_staged_since(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, batch 5 1.4: a repository the killed run created is the user's once they
    stage into it."""
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()
    git = migration_git.git

    def git_then_die(root: Path, *args: str, **kwargs: object) -> object:
        result = git(root, *args, **kwargs)  # type: ignore[arg-type]
        if args[:1] == ("init",):
            raise RuntimeError("killed after git init")
        return result

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "git", git_then_die)
        argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
        assert loadout.main(argv) == 1
    _write(source / "notes.md", "mine\n")
    _git(source, "add", "notes.md")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert str(source / ".git") in json.loads(capsys.readouterr().out)["kept"]
    assert _git(source, "diff", "--cached", "--name-only").stdout == "notes.md\n"


def test_a_folder_init_created_is_its_own_before_the_run_records_it(tmp_path: Path) -> None:
    """Review 2, batch 5 1.5: a folder absent before init and not yet recorded, such as
    an ancestor a write created before a kill, is init's: it is only ever removed once
    empty."""
    folder = tmp_path / "made"
    folder.mkdir()
    plan = migration_backup._Plan({folder: Image()}, {}, {})

    assert plan.ours(folder) is True


def test_a_pre_existing_file_deleted_since_init_is_put_back(tmp_path: Path, capsys) -> None:
    """Review 3, 1.12: restore treated the deletion as an edit to keep, then stopped
    reporting it, and the reduced backup dropped the only copy of the original."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    settings = root / ".claude/settings.json"
    settings.unlink()
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert settings.read_text() == CLAUDE_SETTINGS


def test_a_file_deleted_after_a_first_restore_is_put_back_by_the_next(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    settings, _ = _undo_kept_back_an_edit(root, capsys)
    settings.unlink()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert settings.read_text() == CLAUDE_SETTINGS


def test_restore_never_cleans_a_record_reached_through_a_link_added_since_init(
    tmp_path: Path, capsys
) -> None:
    """Review 3, 1.13: restore reported the record folder kept, then rewrote the record
    inside the link's target anyway."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    record = written_state_path(root)
    folder = record.parent
    elsewhere = tmp_path / "elsewhere"
    shutil.move(folder, elsewhere)
    folder.symlink_to(elsewhere, target_is_directory=True)
    held = (elsewhere / record.name).read_text()
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    report = json.loads(capsys.readouterr().out)
    assert report["reasons"][str(record)] == "now reached through a link added since init"
    assert (elsewhere / record.name).read_text() == held


def test_a_deleted_original_restore_cannot_reach_stays_kept_until_it_is_put_back(
    tmp_path: Path, capsys
) -> None:
    """Review 3, 1.12: an absent path dropped out of `kept`, and the reduced backup with it."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    claude = root / ".claude"
    shutil.rmtree(claude)
    empty = tmp_path / "empty"
    empty.mkdir()
    claude.symlink_to(empty, target_is_directory=True)
    settings = claude / "settings.json"
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1
    assert str(settings) in json.loads(capsys.readouterr().out)["kept"]

    claude.unlink()
    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0
    assert settings.read_text() == CLAUDE_SETTINGS


def test_a_write_killed_mid_record_does_not_block_the_restore(tmp_path: Path, capsys) -> None:
    """Review 3, batch 2.4: the temporary file a killed record write leaves made every
    later restore keep the folder init created and exit 1."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    folder = written_state_path(root).parent
    (folder / ".loadout-tmp-k1ll3d0_").write_text('{"half": ')
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert not folder.exists()


def test_a_folder_init_created_names_the_files_that_keep_it(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    folder = written_state_path(root).parent
    (folder / "notes.txt").write_text("mine\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert (
        reasons[str(folder)] == "a folder init created that now holds files it did not (notes.txt)"
    )


def test_a_folder_holding_only_the_kept_record_says_so(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    _write(root / "loadout/skills/added/SKILL.md", "---\nname: added\ndescription: a\n---\nA.\n")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    folder = written_state_path(root).parent
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert (
        reasons[str(folder)]
        == "a folder init created, kept for the write record restore kept in it"
    )


def test_a_codex_init_with_no_servers_can_be_undone(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, batch 2.5: the record named a `config.toml` sync never created, so restore
    could not finish and left `~/.config/loadout` behind."""
    source = tmp_path / "source"
    source.mkdir()
    before = _home(fake_home)
    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["kept"] == []
    assert _home(fake_home) == before


def test_the_restore_report_lists_what_it_put_back(tmp_path: Path, capsys) -> None:
    """Review 3, batch 2.14: `--json` could not say what, if anything, was restored."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert str(root / ".claude/settings.json") in json.loads(capsys.readouterr().out)["restored"]


def test_a_spent_backup_is_not_named_as_where_the_restore_came_from(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes"]) == 0

    assert capsys.readouterr().out.splitlines()[0] == (
        "Restored what init changed; the backup is spent and was removed."
    )


def test_a_changed_entry_of_a_repository_init_created_is_called_changed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_source(tmp_path / "source", fake_home)
    _git(source, "remote", "add", "origin", "https://example.invalid/me/agents.git")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["reasons"][str(source / ".git/config")] == (
        "changed since init"
    )


def _kill_restore_after_putting_back(root: Path, capsys) -> None:
    assert _init_project(root, "--yes") == 0
    (root / "loadout/instructions/main.md").write_text("# Edited after init\n")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            migration_backup, "_spend", lambda *args: (_ for _ in ()).throw(KeyboardInterrupt)
        )
        with pytest.raises(KeyboardInterrupt):
            loadout.main(["init", "restore", "--yes", "--json"])
    capsys.readouterr()


def test_a_killed_restore_is_announced_as_a_restore(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    _kill_restore_after_putting_back(root, capsys)

    assert _init_project(root, "--json") == 2

    assert json.loads(capsys.readouterr().out)["error"] == (
        "an earlier restore did not finish; finish it with `loadout init restore`"
    )


def test_resuming_a_killed_restore_says_the_earlier_one_did_the_work(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    _kill_restore_after_putting_back(root, capsys)

    assert loadout.main(["init", "restore", "--yes"]) == 1

    assert capsys.readouterr().out.splitlines()[0] == (
        "The earlier restore had already put back everything it could."
    )


def test_a_new_source_repository_swapped_for_a_link_is_kept_whole(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, batch 5.13: removing the repository through the link would have reached
    into whatever it names."""
    source = _global_source(tmp_path / "source", fake_home)
    elsewhere = tmp_path / "elsewhere-git"
    shutil.move(source / ".git", elsewhere)
    (source / ".git").symlink_to(elsewhere, target_is_directory=True)
    held = sorted(path.relative_to(elsewhere) for path in elsewhere.rglob("*"))
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["reasons"][str(source / ".git")] == (
        "changed since init"
    )
    assert sorted(path.relative_to(elsewhere) for path in elsewhere.rglob("*")) == held


def test_a_write_record_from_before_init_is_cleaned_rather_than_put_back(
    tmp_path: Path, capsys
) -> None:
    """Review 3, batch 5.13: putting the record's before-image back would forget what a
    sync wrote since init."""
    root = _project(tmp_path / "p")
    _write(root / "loadout/config.toml", 'harnesses = ["claude"]\n')
    _write(root / "loadout/permissions.toml", '[shell]\ndeny = ["rm"]\n')
    # Forced: the fixture's settings.json holds what this source does not render.
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    shutil.rmtree(root / "loadout")
    assert written_state_path(root).is_file()
    assert _init_project(root, "--yes") == 0
    added = root / ".claude/skills/added/SKILL.md"
    _write(root / "loadout/skills/added/SKILL.md", "---\nname: added\ndescription: a\n---\nA.\n")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    # The record is cleaned, not kept: only what the later sync left stays.
    assert json.loads(capsys.readouterr().out)["kept"] == [
        str(root / ".gitignore"),
        str(root / "loadout"),
        str(root / "loadout/skills"),
    ]
    assert str(added) in json.loads(written_state_path(root).read_text())["written"]


@pytest.mark.parametrize("applied", [True, False])
def test_a_backup_from_another_version_is_refused_by_name_whether_or_not_it_applied(
    tmp_path: Path, applied: bool
) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()
    manifest = backup.directory / "manifest.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "version": 1}))
    if not applied:
        (backup.directory / "applied").unlink()

    with pytest.raises(LoadoutError, match=f"{backup.directory}.*delete that directory"):
        workflow.run_restore(yes=True, as_json=True)


def test_a_backup_whose_manifest_is_not_an_object_is_passed_over(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (backup,) = backups()
    (backup.directory / "manifest.json").write_text("[]\n")

    with pytest.raises(LoadoutError, match="no init backup to restore"):
        workflow.run_restore(yes=True, as_json=True)


def test_a_failed_init_never_names_the_backup_its_restore_spent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 4, batch 2.6: the message named a backup restore had just removed."""
    root = _project(tmp_path / "p")
    _fail_after_deploy(monkeypatch)
    capsys.readouterr()

    assert _init_project(root, "--yes") == 1

    err = capsys.readouterr().err
    assert err == (
        "loadout: init failed (injected after the first render); what it changed was "
        "restored, and the spent backup was removed\n"
    )
    assert backups() == ()


def test_a_kept_path_with_no_recorded_reason_is_not_init_s_to_undo(capsys) -> None:
    kept = Path("/x/notes.md")

    workflow._print_kept(migration_backup.Restored((kept,), {}, {}, ()), sys.stdout)

    assert capsys.readouterr().out.startswith(f"Kept, not init's to undo: {kept}\n")


def test_a_folder_init_created_is_not_blamed_on_the_folders_restore_kept_in_it(
    tmp_path: Path, capsys
) -> None:
    """Review 4, batch 2.6: a child folder init created and restore kept was listed as a
    file init did not write, and the reason ran into the path with a second colon."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    skill = root / "loadout/skills"
    created = skill / "review"
    (created / "notes.txt").write_text("mine\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert (
        reasons[str(created)] == "a folder init created that now holds files it did not (notes.txt)"
    )
    assert reasons[str(skill)] == "a folder init created, kept for what restore kept inside it"


def test_a_write_killed_beside_an_existing_file_leaves_no_temporary_after_restore(
    tmp_path: Path, capsys
) -> None:
    """Review 4, batch 2.6: only folders init created were swept, so a kill at the rename
    over a file that was already there left its temporary file beside it."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    (root / ".loadout-tmp-k1ll3d0_").write_text("half")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert not (root / ".loadout-tmp-k1ll3d0_").exists()
    assert (root / "CLAUDE.md").is_file()


def test_the_next_init_at_a_terminal_offers_to_finish_a_killed_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 4, batch 4.1: the restore's own wording was pinned only under --json."""
    root = _project(tmp_path / "p")
    _kill_restore_after_putting_back(root, capsys)
    prompts = _terminal(monkeypatch, "n")

    assert _init_project(root) == 2

    assert prompts[0].startswith("An earlier restore did not finish.")


def test_a_folder_init_created_names_three_files_and_counts_the_rest(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    folder = written_state_path(root).parent
    for name in ("a.txt", "b.txt", "c.txt", "d.txt", "e.txt"):
        (folder / name).write_text("mine\n")
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    reasons = json.loads(capsys.readouterr().out)["reasons"]
    assert reasons[str(folder)] == (
        "a folder init created that now holds files it did not (a.txt, b.txt, c.txt and 2 more)"
    )


def test_a_link_named_like_an_interrupted_write_is_never_removed(tmp_path: Path, capsys) -> None:
    """Only a file of loadout's own is swept: a link there points at someone else's."""
    root = _project(tmp_path / "p")
    assert _init_project(root, "--yes") == 0
    folder = written_state_path(root).parent
    mine = _write(tmp_path / "mine.txt", "mine\n")
    link = folder / ".loadout-tmp-l1nkl1nk"
    link.symlink_to(mine)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 1

    assert (link.readlink(), mine.read_text()) == (mine, "mine\n")


def test_restore_never_removes_a_user_s_file_named_like_loadout_s(tmp_path: Path, capsys) -> None:
    """Review 5, batch 1.1: every `.loadout-*` file beside a restored path was deleted, a
    committed `.loadout-notes.md` included. Only the exact shape of loadout's own
    temporary file, beside a path in the backup, is swept."""
    root = _project(tmp_path / "p", repository=False)
    kept = {
        root / ".loadout-notes.md": "notes\n",
        root / ".claude/.loadout-mine": "mine\n",
        root / ".loadout-tmp-notes.md": "not loadout's shape\n",
        root / "docs/.loadout-tmp-abcdefgh": "no backed-up path beside it\n",
    }
    for path, content in kept.items():
        _write(path, content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    assert _init_project(root, "--yes") == 0
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert {path: path.read_text() for path in kept} == kept


def test_restore_never_removes_a_file_named_like_loadout_s_in_home(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 5, batch 1.1: the home folder is beside `~/.claude.json`, so its
    `.loadout-*` files went too."""
    mine = _write(fake_home / ".loadout-notes.md", "notes\n")
    _write(fake_home / ".claude.json", json.dumps({"numStartups": 1}))
    _global_source(tmp_path / "source", fake_home)
    capsys.readouterr()

    assert loadout.main(["init", "restore", "--yes", "--json"]) == 0

    assert mine.read_text() == "notes\n"
