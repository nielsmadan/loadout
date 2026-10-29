"""init either converts or puts everything back (ADR 0033).

The backup lives for one run: a success deletes it, a failure puts every path back from it
and deletes it, and a run killed part way leaves it for the next init to put back first.
init commits nothing and stages nothing but the untracking of the outputs it ignores.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout import migration_git
from loadout.migration_backup import backups_root, interrupted_backup

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
    """Every entry under `root` with its bytes or link target, and its mode."""
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
    """HOME without the backups and the directories that hold them."""
    holders = {backups_root(), *backups_root().parents}
    return {
        key: value
        for key, value in _snapshot(home, skip=(backups_root(),)).items()
        if home / key not in holders
    }


def _backups() -> list[str]:
    root = backups_root()
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def _init_project(root: Path, *extra: str) -> int:
    return loadout.main(["init", "project", "--root", str(root), "--harness", "claude", *extra])


def _fail_after_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail once init has applied every operation and written the first render."""
    deploy = workflow.deploy

    def failing(*args: object, **kwargs: object) -> object:
        deploy(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("injected after the first render")

    monkeypatch.setattr(workflow, "deploy", failing)


def _terminal(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    prompts: list[str] = []
    replies = iter(answers)

    def reply(prompt: str) -> str:
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(workflow, "_answer", reply)
    return prompts


def _read_only(path: Path) -> None:
    path.chmod(stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP)


def _at_restore(monkeypatch: pytest.MonkeyPatch, look: object) -> list[object]:
    """What `look` sees on disk as the restore starts, so a test proves the run had
    written something before it failed."""
    seen: list[object] = []
    restore = workflow.restore

    def looking(backup: object) -> object:
        seen.append(look())  # type: ignore[operator]
        return restore(backup)  # type: ignore[arg-type]

    monkeypatch.setattr(workflow, "restore", looking)
    return seen


def test_a_success_deletes_its_backup(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")

    assert _init_project(root, "--yes", "--json") == 0

    assert json.loads(capsys.readouterr().out)["status"] == "complete"
    assert (root / "loadout/config.toml").is_file()
    assert _backups() == []


def test_a_render_failing_part_way_puts_back_the_files_their_modes_and_the_index(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A real failure, after the source, the .gitignore and the untracking are written:
    the first render cannot rewrite .claude/settings.json in a folder it may not change."""
    root = _project(tmp_path / "p")
    ignore = _write(root / ".gitignore", "*.log\n")
    ignore.chmod(0o600)
    _read_only(root / ".claude")
    staged_before = _git(root, "ls-files", "-s").stdout
    before = _snapshot(root)
    home_before = _home(fake_home)
    seen = _at_restore(
        monkeypatch,
        lambda: (
            (root / "loadout/config.toml").is_file(),
            ignore.read_text() == "*.log\n",
            _git(root, "ls-files", "-s").stdout == staged_before,
        ),
    )

    assert _init_project(root, "--yes") == 1

    assert seen == [(True, False, False)]
    error = capsys.readouterr().err
    assert "Permission denied" in error
    assert "everything it changed was put back" in error
    assert _snapshot(root) == before
    assert _git(root, "ls-files", "-s").stdout == staged_before
    assert _home(fake_home) == home_before
    assert _backups() == []


def test_a_global_render_failing_part_way_puts_back_the_link_and_removes_the_new_repository(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """A real failure after init replaced a linked ~/.claude with a directory, created the
    source's repository and registered it: Codex's folder cannot take its AGENTS.md."""
    dotfiles = tmp_path / "dotfiles"
    _write(dotfiles / "CLAUDE.md", "# Me\n")
    _write(dotfiles / "settings.json", '{"permissions": {"allow": ["Bash(git status)"]}}\n')
    _write(dotfiles / "commands/foo.md", "Do foo.\n")
    (fake_home / ".claude").symlink_to(dotfiles, target_is_directory=True)
    _write(fake_home / ".codex/AGENTS.md", "# Codex\n").chmod(0o600)
    _read_only(fake_home / ".codex")
    source = tmp_path / "source"
    source.mkdir()
    dotfiles_before = _snapshot(dotfiles)
    home_before = _home(fake_home)
    claude = fake_home / ".claude"
    seen = _at_restore(
        monkeypatch,
        lambda: ((source / ".git").is_dir(), claude.is_dir() and not claude.is_symlink()),
    )

    argv = ["init", "global", str(source), "--harness", "claude", "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 1

    assert seen == [(True, True)]
    assert "everything it changed was put back" in capsys.readouterr().err
    assert (fake_home / ".claude").readlink() == dotfiles
    assert _snapshot(dotfiles) == dotfiles_before
    assert _home(fake_home) == home_before
    assert list(source.iterdir()) == []
    assert _backups() == []


def test_a_failure_after_the_first_render_puts_back_every_path(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    home_before = _home(fake_home)
    _fail_after_deploy(monkeypatch)

    assert _init_project(root, "--yes") == 1

    assert "init failed (injected after the first render)" in capsys.readouterr().err
    assert _snapshot(root) == before
    assert _home(fake_home) == home_before
    assert _backups() == []


def test_a_failure_through_a_written_through_config_puts_back_the_dotfiles_file(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """~/.config linked into a dotfiles repo is written through (ADR 0023), so the
    dotfiles copy is the one put back."""
    config = tmp_path / "dotfiles/config"
    agents = _write(config / "opencode/AGENTS.md", "# oc\n")
    (fake_home / ".config").symlink_to(config, target_is_directory=True)
    source = tmp_path / "source"
    source.mkdir()
    _fail_after_deploy(monkeypatch)

    assert loadout.main(["init", "global", str(source), "--harness", "opencode", "--yes"]) == 1

    assert agents.read_text() == "# oc\n"
    assert (fake_home / ".config").readlink() == config
    assert _backups() == []


def test_a_first_render_that_differs_from_the_preview_puts_back_every_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    compare = workflow.compare_with_disk
    calls: list[object] = []

    def second_differs(outputs: object) -> object:
        calls.append(outputs)
        return compare(outputs) if len(calls) == 1 else []  # type: ignore[arg-type]

    monkeypatch.setattr(workflow, "compare_with_disk", second_differs)

    assert _init_project(root, "--yes") == 1

    assert "the first render differs from the preview" in capsys.readouterr().err
    assert _snapshot(root) == before


def _interrupt_after_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    deploy = workflow.deploy

    def interrupted(*args: object, **kwargs: object) -> object:
        deploy(*args, **kwargs)  # type: ignore[arg-type]
        raise KeyboardInterrupt

    monkeypatch.setattr(workflow, "deploy", interrupted)


def test_an_interrupt_during_apply_puts_back_every_path_and_exits_130(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Ctrl-C is not an Exception, and must not skip the restore; the user stopped it, so
    the skill is recommended but nothing is asked, and init exits as an interrupt does."""
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    _interrupt_after_deploy(monkeypatch)
    prompts = _terminal(monkeypatch)

    assert _init_project(root, "--yes") == 130

    assert _snapshot(root) == before
    assert prompts == []
    assert workflow.SKILL_ADVICE in capsys.readouterr().err


def test_an_interrupt_under_json_prints_only_the_restored_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _interrupt_after_deploy(monkeypatch)

    assert _init_project(root, "--yes", "--json") == 130

    document = json.loads(capsys.readouterr().out)
    assert (document["status"], document["exit_code"]) == ("restored", 130)


def test_a_failure_whose_put_back_fails_leaves_the_backup_for_the_next_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    with pytest.MonkeyPatch.context() as patch:
        _fail_after_deploy(patch)

        def refused(*args: object) -> None:
            raise OSError("disk full")

        patch.setattr("loadout.migration_backup.install", refused)
        assert _init_project(root, "--yes", "--json") == 1
    (name,) = _backups()
    document = json.loads(capsys.readouterr().out)
    _terminal(monkeypatch, "y")

    assert _init_project(root) == 0

    assert document == {
        "status": "interrupted",
        "exit_code": 1,
        "error": (
            "init failed (injected after the first render), and putting back what it changed "
            "failed too (disk full); the next init puts it back first"
        ),
        "backup": str(backups_root() / name),
    }
    assert _snapshot(root) == before


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
    assert _backups() == []
    assert _snapshot(root) == before
    assert _init_project(root, "--yes") == 0


def test_the_backup_is_readable_only_by_its_owner_and_holds_each_image_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5.3: before-images can hold anything the adopted files held. Read while the
    run applies, since a success deletes the backup."""
    root = _project(tmp_path / "p")
    seen: dict[str, object] = {}
    apply = workflow.apply_migration

    def inspecting(prepared: object) -> object:
        (directory,) = backups_root().iterdir()
        manifest = json.loads((directory / "manifest.json").read_text())
        images = directory / "images"
        seen.update(
            modes={
                "root": backups_root().stat().st_mode & 0o777,
                "backup": directory.stat().st_mode & 0o777,
                "images": images.stat().st_mode & 0o777,
                "manifest": (directory / "manifest.json").stat().st_mode & 0o777,
            },
            image_modes={p.stat().st_mode & 0o777 for p in images.iterdir()},
            settings=next(
                e for e in manifest["entries"] if e["path"].endswith(".claude/settings.json")
            ),
            stored={p.name: p.read_bytes() for p in images.iterdir()},
        )
        return apply(prepared)  # type: ignore[arg-type]

    monkeypatch.setattr(workflow, "apply_migration", inspecting)

    assert _init_project(root, "--yes") == 0

    assert seen["modes"] == {"root": 0o700, "backup": 0o700, "images": 0o700, "manifest": 0o600}
    assert seen["image_modes"] == {0o600}
    digest = seen["settings"]["sha256"]  # type: ignore[index]
    assert seen["stored"][digest] == CLAUDE_SETTINGS.encode()  # type: ignore[index]


def test_a_failure_never_removes_a_user_s_file_named_like_loadout_s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 5, batch 1.1: only the exact shape of loadout's own temporary file, beside a
    path in the backup, is swept."""
    root = _project(tmp_path / "p", repository=False)
    mine = _write(tmp_path / "mine.txt", "mine\n")
    kept = {
        root / ".loadout-notes.md": "notes\n",
        root / ".claude/.loadout-mine": "mine\n",
        root / ".loadout-tmp-notes.md": "not loadout's shape\n",
        root / ".loadout-tmp-abc": "too short to be loadout's\n",
        root / ".loadout-tmp-xxxxxxxx.bak": "loadout's shape and more\n",
        root / "docs/.loadout-tmp-abcdefgh": "no backed-up path beside it\n",
        root / ".loadout-tmp-aaaaaaaa/inside.txt": "a folder, not a write\n",
    }
    for path, content in kept.items():
        _write(path, content)
    (root / ".loadout-tmp-l1nkl1nk").symlink_to(mine)
    deploy = workflow.deploy

    def killed_mid_write(*args: object, **kwargs: object) -> object:
        deploy(*args, **kwargs)  # type: ignore[arg-type]
        (root / ".loadout-tmp-zzzzzzzz").write_text("half")
        raise RuntimeError("killed at a rename")

    monkeypatch.setattr(workflow, "deploy", killed_mid_write)

    assert _init_project(root, "--yes") == 1

    assert {path: path.read_text() for path in kept} == kept
    assert (root / ".loadout-tmp-l1nkl1nk").readlink() == mine
    assert sorted(p.name for p in root.glob(".loadout-tmp-*")) == [
        ".loadout-tmp-aaaaaaaa",
        ".loadout-tmp-abc",
        ".loadout-tmp-l1nkl1nk",
        ".loadout-tmp-notes.md",
        ".loadout-tmp-xxxxxxxx.bak",
    ]


def _killed(patch: pytest.MonkeyPatch) -> None:
    """A kill never reaches the restore, so the backup stays marked in progress."""
    patch.setattr(workflow, "_failed", lambda *args, **kwargs: 1)


def _killed_init(root: Path) -> None:
    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        _fail_after_deploy(patch)
        assert _init_project(root, "--yes") == 1


def test_the_next_init_at_a_terminal_puts_back_a_killed_one_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    before = _snapshot(root)
    _killed_init(root)
    prompts = _terminal(monkeypatch, "y")

    assert _init_project(root) == 0

    assert prompts == [
        (
            f"An earlier init of {root} was stopped part way. Put back everything it changed? "
            "Edits made since to those files are lost. [y/N] "
        )
    ]
    assert "as they were before that init; run init again." in capsys.readouterr().out
    assert _snapshot(root) == before
    assert _backups() == []


def test_declining_to_put_back_a_killed_init_keeps_its_backup_and_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _killed_init(root)
    (name,) = _backups()
    _terminal(monkeypatch, "n")

    assert _init_project(root) == 2

    assert "init stops here until that is put back" in capsys.readouterr().out
    assert interrupted_backup().directory.name == name  # type: ignore[union-attr]


@pytest.mark.parametrize("as_json", [False, True])
def test_without_a_terminal_a_killed_init_refuses_and_says_how_to_proceed(
    tmp_path: Path, capsys, as_json: bool
) -> None:
    root = _project(tmp_path / "p")
    _killed_init(root)
    (name,) = _backups()
    capsys.readouterr()
    error = (
        "an earlier init was stopped part way and left what it changed in place; "
        "run init again at a terminal to put that back first"
    )

    assert _init_project(root, "--yes", *(["--json"] if as_json else [])) == 2

    captured = capsys.readouterr()
    if as_json:
        assert json.loads(captured.out) == {
            "status": "interrupted",
            "exit_code": 2,
            "backup": str(backups_root() / name),
            "error": error,
        }
    else:
        assert f"loadout: {error} (backup {backups_root() / name})" in captured.err


def test_a_run_killed_after_untracking_has_its_index_put_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    staged_before = _git(root, "ls-files", "-s").stdout
    untrack = migration_git.untrack

    def untrack_then_die(*args: object) -> None:
        untrack(*args)  # type: ignore[arg-type]
        raise RuntimeError("killed after untracking")

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "untrack", untrack_then_die)
        assert _init_project(root, "--yes") == 1
    _terminal(monkeypatch, "y")

    assert _init_project(root) == 0

    assert _git(root, "ls-files", "-s").stdout == staged_before


def test_a_run_killed_after_git_init_has_the_new_repository_removed(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    source = tmp_path / "source"
    source.mkdir()
    git = migration_git.git
    argv = ["init", "global", str(source), "--harness", "claude"]

    def git_then_die(root: Path, *args: str, **kwargs: object) -> object:
        result = git(root, *args, **kwargs)  # type: ignore[arg-type]
        if args[:1] == ("init",):
            raise RuntimeError("killed after git init")
        return result

    with pytest.MonkeyPatch.context() as patch:
        _killed(patch)
        patch.setattr(migration_git, "git", git_then_die)
        assert loadout.main([*argv, "--yes"]) == 1
    assert (source / ".git").is_dir()
    _terminal(monkeypatch, "y")

    assert loadout.main(argv) == 0

    assert list(source.iterdir()) == []


def test_a_damaged_image_refuses_to_put_back_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _killed_init(root)
    (name,) = _backups()
    images = backups_root() / name / "images"
    for image in images.iterdir():
        image.write_text("{}\n")
    adopted = _snapshot(root)
    _terminal(monkeypatch, "y")

    assert _init_project(root) == 3

    assert "is damaged or missing; nothing was restored" in capsys.readouterr().err
    assert _snapshot(root) == adopted


def test_a_backup_from_another_version_is_refused_with_the_directory_to_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _killed_init(root)
    (name,) = _backups()
    manifest = backups_root() / name / "manifest.json"
    manifest.write_text(json.dumps({**json.loads(manifest.read_text()), "version": 4}))
    _terminal(monkeypatch, "y")

    assert _init_project(root) == 3

    assert (
        f"backup {backups_root() / name} was written by another version of loadout and "
        "cannot be restored; delete that directory" in capsys.readouterr().err
    )


SKILL_COMMAND = "loadout integrate skill copy --harness claude --harness codex --harness droid"


def _unconvertible(root: Path) -> Path:
    """A setup init cannot convert on its own: a settings file that does not parse."""
    _write(root / ".claude/settings.json", '{"permissions": ')
    _write(root / "CLAUDE.md", "# Project\n")
    return root


def _init_three(root: Path, *extra: str) -> int:
    harnesses = ("--harness", "claude", "--harness", "codex", "--harness", "droid")
    return loadout.main(["init", "project", "--root", str(root), *harnesses, *extra])


def test_an_incomplete_plan_without_a_terminal_recommends_the_skill_and_its_command(
    tmp_path: Path, capsys
) -> None:
    root = _unconvertible(tmp_path / "p")

    assert _init_three(root) == 2

    error = capsys.readouterr().err
    assert f"{workflow.SKILL_ADVICE}\nTo install it: {SKILL_COMMAND}\n" in error


def test_an_incomplete_plan_under_json_carries_the_recommendation_and_command(
    tmp_path: Path, capsys
) -> None:
    root = _unconvertible(tmp_path / "p")

    assert _init_three(root, "--json") == 2

    document = json.loads(capsys.readouterr().out)
    assert (document["status"], document["exit_code"]) == ("incomplete", 2)
    assert (document["recommendation"], document["install_command"]) == (
        workflow.SKILL_ADVICE,
        SKILL_COMMAND,
    )


def test_a_failure_under_json_carries_what_was_put_back_and_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _fail_after_deploy(monkeypatch)

    assert _init_project(root, "--yes", "--json") == 1

    document = json.loads(capsys.readouterr().out)
    assert (document["status"], document["exit_code"]) == ("restored", 1)
    assert document["error"] == "injected after the first render"
    assert str(root / "loadout") in document["restored"]
    assert document["install_command"] == "loadout integrate skill copy --harness claude"


def test_a_refusal_a_flag_answers_names_the_flag_and_not_the_skill(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The skill cannot choose a registration for the user."""
    _write(fake_home / ".claude/CLAUDE.md", "# Me\n")
    machine = _write(fake_home / ".config/loadout/config.toml", 'source = "/different"\n')
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 2

    error = capsys.readouterr().err
    assert error.splitlines()[-2:] == [
        (
            "registration-conflict: Run init with --registration replace to register this "
            "source; --yes does not choose ownership."
        ),
        f"  {machine}",
    ]
    assert workflow.SKILL_ADVICE not in error


def _skill_folders(home: Path) -> list[Path]:
    return [
        home / ".claude/skills/loadout",
        home / ".agents/skills/loadout",
        home / ".factory/skills/loadout",
    ]


def test_at_a_terminal_yes_copies_the_skill_into_each_harness_s_user_folder(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _unconvertible(tmp_path / "p")
    prompts = _terminal(monkeypatch, "y")

    assert _init_three(root) == 2

    assert prompts == [
        (
            "Install the /loadout skill into your current setup so you can run the conversion "
            "with it? [y/N] "
        )
    ]
    bundled = Path(loadout.__file__).parent / "_skills/loadout"
    expected = {p.relative_to(bundled): p.read_bytes() for p in bundled.rglob("*") if p.is_file()}
    for folder in _skill_folders(fake_home):
        assert {
            p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()
        } == expected
    assert capsys.readouterr().out.splitlines()[-6:] == [
        *(f"Copied the loadout skill to {folder}" for folder in _skill_folders(fake_home)),
        "In claude, type /loadout.",
        "In codex, type $loadout.",
        "In droid, type /loadout.",
    ]


def test_at_a_terminal_a_differing_copy_is_refused_and_nothing_is_copied(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _unconvertible(tmp_path / "p")
    theirs = _write(fake_home / ".agents/skills/loadout/SKILL.md", "my own loadout skill\n")
    _terminal(monkeypatch, "y")

    assert _init_three(root) == 2

    assert (
        f"loadout: a different loadout skill is already at {theirs.parent}; nothing was "
        "copied. Remove it, or use the copy that is there" in capsys.readouterr().err
    )
    assert theirs.read_text() == "my own loadout skill\n"
    assert not (fake_home / ".claude/skills/loadout").exists()


def test_a_failed_init_at_a_terminal_offers_the_skill_and_honours_a_no(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _project(tmp_path / "p")
    _fail_after_deploy(monkeypatch)
    prompts = _terminal(monkeypatch, "y", "n")

    assert _init_project(root) == 1

    assert prompts[-1].startswith("Install the /loadout skill into your current setup")
    assert not (fake_home / ".claude/skills/loadout").exists()


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

    assert sorted(p.name for p in root.iterdir()) == [
        ".claude",
        ".gitignore",
        "CLAUDE.md",
        "loadout",
    ]


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


def test_a_project_with_only_claude_md_ignores_it_once(tmp_path: Path, capsys) -> None:
    """Review I."""
    root = _write(tmp_path / "p/CLAUDE.md", "# Project\n").parent
    _git(root, "init", "-q")

    assert _init_project(root, "--yes") == 0

    assert (root / ".gitignore").read_text().splitlines().count("/CLAUDE.md") == 1


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
    assert (written.is_symlink(), 'model = "gpt-5"' in written.read_text()) == (False, True)
    assert config.read_text() == 'model = "gpt-5"\n'


def test_every_init_json_answer_carries_status_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 2.11: one envelope on every path."""
    root = _project(tmp_path / "p")
    unconvertible = _unconvertible(tmp_path / "u")
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--json"]
    answers = []

    def answer(arguments: list[str], code: int) -> None:
        assert loadout.main(arguments) == code
        envelope = json.loads(capsys.readouterr().out)
        answers.append((envelope["status"], envelope["exit_code"]))

    answer([*argv, "--dry-run"], 0)
    answer(["init", "project", "--root", str(unconvertible), "--json"], 2)
    with pytest.MonkeyPatch.context() as patch:
        _fail_after_deploy(patch)
        answer([*argv, "--yes"], 1)
    _killed_init(root)
    capsys.readouterr()
    answer([*argv, "--yes"], 2)

    assert answers == [("preview", 0), ("incomplete", 2), ("restored", 1), ("interrupted", 2)]


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
