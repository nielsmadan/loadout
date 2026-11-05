from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

import loadout
from loadout import files, overlay_lock
from registration import register

pytestmark = pytest.mark.integration

PI_MANIFEST = '[[source]]\nname = "s"\npath = "."\n\n[pi]\nplugins = ["kit"]\n'


def _source(root: Path) -> Path:
    (root / "plugins").mkdir(parents=True)
    _set_packages(root, {"docs": {"source": "npm:docs-pkg"}})
    (root / "loadout.toml").write_text(PI_MANIFEST)
    return register(root)


def _set_packages(root: Path, plugins: dict[str, dict[str, str]]) -> None:
    (root / "plugins" / "kit.json").write_text(json.dumps({"plugins": plugins}))


def _add_package(root: Path) -> None:
    _set_packages(root, {"docs": {"source": "npm:docs-pkg"}, "more": {"source": "npm:more-pkg"}})


def test_a_global_sync_waits_for_pis_lock_then_fails_without_writing(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".pi" / "agent" / "settings.json"
    before = document.read_bytes()
    _add_package(root)
    lock_dir = Path(f"{document}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    started = time.monotonic()
    assert loadout.main(["sync", "--root", str(root)]) == 3
    elapsed = time.monotonic() - started

    assert 0.3 <= elapsed < 10
    assert str(lock_dir) in capsys.readouterr().err
    assert document.read_bytes() == before
    assert lock_dir.is_dir()


def test_a_global_sync_writes_pi_settings_and_leaves_no_lock_directory(
    tmp_path: Path, fake_home: Path
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".pi" / "agent" / "settings.json"
    before = document.read_bytes()
    _add_package(root)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert b"npm:more-pkg" in document.read_bytes()
    assert document.read_bytes() != before
    assert not Path(f"{document}.lock").exists()


def test_a_symlinked_pi_settings_is_locked_beside_the_link_not_its_target(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    link = fake_home / ".pi" / "agent" / "settings.json"
    target = tmp_path / "elsewhere" / "pi-settings.json"
    target.parent.mkdir()
    link.rename(target)
    link.symlink_to(target)
    before = target.read_bytes()
    _add_package(root)
    lock_dir = Path(f"{link}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert str(lock_dir) in capsys.readouterr().err
    assert target.read_bytes() == before
    assert not Path(f"{target}.lock").exists()


def test_a_sync_that_retires_the_global_pi_settings_waits_for_pis_lock(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".pi" / "agent" / "settings.json"
    before = document.read_bytes()
    assert b"npm:docs-pkg" in before
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n\n[pi]\n')
    lock_dir = Path(f"{document}.lock")
    lock_dir.mkdir()
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert str(lock_dir) in capsys.readouterr().err
    assert document.read_bytes() == before
    assert lock_dir.is_dir()


def test_a_pi_agent_dir_that_does_not_exist_yet_is_created_and_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_dir = tmp_path / "pi-agent"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    root = _source(tmp_path / "source")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert "npm:docs-pkg" in (agent_dir / "settings.json").read_text(encoding="utf-8")
    assert not (agent_dir / "settings.json.lock").exists()


def test_a_pi_save_between_render_and_lock_fails_the_sync_and_keeps_pis_save(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / ".pi" / "agent" / "settings.json"
    _add_package(root)
    saved = json.loads(document.read_text(encoding="utf-8"))
    saved["lastChangelogVersion"] = "9.9.9"
    pi_save = (json.dumps(saved, indent=2) + "\n").encode()
    real_lock = files.harness_lock

    def lock_after_pi_saves(path: Path) -> AbstractContextManager[None]:
        if path == document:
            document.write_bytes(pi_save)
        return real_lock(path)

    monkeypatch.setattr(files, "harness_lock", lock_after_pi_saves)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"Pi saved {document} during the sync" in capsys.readouterr().err
    assert document.read_bytes() == pi_save
