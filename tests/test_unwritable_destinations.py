"""A folder loadout cannot write into is a configuration error naming it, exit 3, not a
PermissionError traceback, exit 4."""

from __future__ import annotations

import errno
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout import cli
from registration import register

pytestmark = pytest.mark.integration


def _unwritable(path: Path, folder: Path) -> str:
    return f"loadout: cannot write {path}: {folder} is not writable (Permission denied)\n"


@pytest.fixture
def global_source(tmp_path: Path, fake_home: Path, capsys) -> Path:
    root = tmp_path / "source"
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/main.md").write_text("# one\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\ninstructions = ["main"]\n'
    )
    (fake_home / ".claude").mkdir()
    register(root)
    assert loadout.main(["sync", "--global"]) == 0
    (root / "instructions/main.md").write_text("# two\n")
    capsys.readouterr()
    return root


@pytest.fixture
def project(tmp_path: Path, capsys) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# one\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"]) == 0
    )
    (root / "loadout/instructions/main.md").write_text("# two\n")
    capsys.readouterr()
    return root


def test_global_sync_into_a_read_only_folder_names_it(
    global_source: Path, fake_home: Path, capsys
) -> None:
    claude = fake_home / ".claude"
    claude.chmod(0o555)

    assert loadout.main(["sync", "--global"]) == 3
    assert capsys.readouterr().err == _unwritable(claude / "CLAUDE.md", claude)
    assert loadout.main(["check", "--global"]) == 1


def test_project_sync_into_a_read_only_root_names_it(project: Path, capsys) -> None:
    project.chmod(0o555)

    assert loadout.main(["sync", "--root", str(project)]) == 3
    assert capsys.readouterr().err == _unwritable(project / "CLAUDE.md", project)


def test_a_post_merge_hook_into_a_read_only_root_names_it(project: Path, capsys) -> None:
    project.chmod(0o555)

    assert loadout.main(["hook-event", "post-merge", "--root", str(project), "--", "0"]) == 3
    assert capsys.readouterr().err.startswith(_unwritable(project / "CLAUDE.md", project))


def test_init_without_a_writable_state_folder_names_it(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o555)
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    root = tmp_path / "p"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# one\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 3
    assert capsys.readouterr().err == _unwritable(state / "loadout/backups", state)
    assert sorted(p.name for p in root.iterdir()) == [".git", "CLAUDE.md"]


def test_completions_into_a_read_only_folder_name_it(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o555)
    monkeypatch.setenv("XDG_DATA_HOME", str(data))

    assert loadout.main(["integrate", "completions", "install", "--shell", "zsh"]) == 3
    err = capsys.readouterr().err
    assert err.startswith(f"loadout: cannot write {data}/")
    assert err.endswith(f": {data} is not writable (Permission denied)\n")


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        (errno.EACCES, "Permission denied"),
        (errno.EPERM, "Operation not permitted"),
        (errno.EROFS, "Read-only file system"),
    ],
)
def test_any_other_denied_write_is_named_without_a_traceback(
    capsys, monkeypatch: pytest.MonkeyPatch, code: int, reason: str
) -> None:
    def denied(args: object) -> int:
        raise OSError(code, reason, "/somewhere/file")

    monkeypatch.setattr(cli, "_dispatch", denied)
    assert loadout.main(["sync"]) == 3
    assert capsys.readouterr().err == f"loadout: /somewhere/file: {reason}\n"


def test_an_unexpected_os_error_is_still_internal(capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(args: object) -> int:
        raise OSError(errno.EIO, "Input/output error", "/somewhere/file")

    monkeypatch.setattr(cli, "_dispatch", broken)
    assert loadout.main(["sync"]) == 4
    assert capsys.readouterr().err.startswith("Traceback")
