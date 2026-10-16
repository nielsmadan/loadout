"""init protects an adoption with a before-image backup, not a journalled transaction (D-06).

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
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout import migration_git
from loadout.errors import LoadoutError
from loadout.migration_backup import backups, backups_root
from loadout.written import written_state_path

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

    assert json.loads(capsys.readouterr().out)["kept"]
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


def test_a_run_killed_after_git_init_removes_the_new_repository(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
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
    monkeypatch.setattr(workflow, "_first_sync", lambda *args, **kwargs: 1)

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
