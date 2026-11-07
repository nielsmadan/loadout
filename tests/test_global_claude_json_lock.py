from __future__ import annotations

import time
from pathlib import Path

import pytest

import loadout
from loadout import stealth_lock
from registration import register

pytestmark = pytest.mark.integration


def _source(root: Path) -> Path:
    root.mkdir()
    (root / "mcp.toml").write_text('[docs]\ntransport = "stdio"\ncommand = "docs-mcp"\n')
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\nmcp = true\n'
    )
    return register(root)


def _add_server(root: Path) -> None:
    with (root / "mcp.toml").open("a") as handle:
        handle.write('\n[more]\ntransport = "stdio"\ncommand = "more-mcp"\n')


def test_a_global_sync_waits_for_claudes_lock_then_fails_without_writing(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".claude.json"
    before = document.read_bytes()
    _add_server(root)
    lock_dir = Path(f"{document}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(stealth_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    started = time.monotonic()
    assert loadout.main(["sync", "--root", str(root)]) == 3
    elapsed = time.monotonic() - started

    assert 0.3 <= elapsed < 10
    assert str(lock_dir) in capsys.readouterr().err
    assert document.read_bytes() == before
    assert lock_dir.is_dir()


def test_a_global_sync_writes_claude_json_and_leaves_no_lock_directory(
    tmp_path: Path, fake_home: Path
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".claude.json"
    before = document.read_bytes()
    _add_server(root)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert b"more-mcp" in document.read_bytes()
    assert document.read_bytes() != before
    assert not Path(f"{document}.lock").exists()


def test_a_symlinked_claude_json_is_locked_beside_the_link_not_its_target(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    link = fake_home / ".claude.json"
    target = tmp_path / "elsewhere" / "claude-state.json"
    target.parent.mkdir()
    link.rename(target)
    link.symlink_to(target)
    before = target.read_bytes()
    _add_server(root)
    lock_dir = Path(f"{link}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(stealth_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert str(lock_dir) in capsys.readouterr().err
    assert target.read_bytes() == before
    assert not Path(f"{target}.lock").exists()


def test_a_sync_that_retires_the_global_claude_json_output_waits_for_claudes_lock(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".claude.json"
    before = document.read_bytes()
    assert b"docs-mcp" in before
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\nmcp = false\n'
    )
    lock_dir = Path(f"{document}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(stealth_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert str(lock_dir) in capsys.readouterr().err
    assert document.read_bytes() == before
    assert lock_dir.is_dir()


def test_a_claude_config_dir_that_does_not_exist_yet_is_created_and_written(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_dir = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    root = _source(tmp_path / "source")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert "docs" in (config_dir / ".claude.json").read_text(encoding="utf-8")
    assert not (config_dir / ".claude.json.lock").exists()
