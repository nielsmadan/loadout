from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.written import record_written, text_entry, written_state_path

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def scratch_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(state))
    return state


def _project(root: Path) -> Path:
    (root / "loadout" / "instructions").mkdir(parents=True)
    (root / "loadout" / "instructions" / "main.md").write_text("Shared rules.\n")
    (root / "loadout" / "config.toml").write_text(
        'harnesses = ["claude"]\ninstructions = ["main"]\n'
    )
    return root


def _dead_record(root: Path) -> Path:
    root.mkdir()
    record_written(root, "default", {root / "CLAUDE.md": text_entry(root / "CLAUDE.md", "x\n")})
    root.rmdir()
    return written_state_path(root)


def _files(root: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_sync_deletes_the_record_of_a_vanished_root_and_keeps_the_live_one(
    tmp_path: Path,
) -> None:
    live = _project(tmp_path / "live")
    assert loadout.main(["sync", "--root", str(live)]) == 0
    record = written_state_path(live)
    before = record.read_bytes()
    files = _files(live)
    dead = _dead_record(tmp_path / "gone")
    assert dead.is_file()

    assert loadout.main(["sync", "--root", str(live)]) == 0

    assert not dead.exists()
    assert record.read_bytes() == before
    assert _files(live) == files


def test_check_leaves_the_state_directory_unchanged(
    tmp_path: Path, scratch_state: Path, capsys
) -> None:
    live = _project(tmp_path / "live")
    assert loadout.main(["sync", "--root", str(live)]) == 0
    dead = _dead_record(tmp_path / "gone")
    before = _files(scratch_state)
    assert dead in before

    assert loadout.main(["check", "--root", str(live), "--json"]) == 0
    assert loadout.main(["check", "--root", str(live)]) == 0

    assert _files(scratch_state) == before
    assert '"status": "clean"' in capsys.readouterr().out


def test_a_root_that_cannot_be_examined_keeps_its_record(tmp_path: Path) -> None:
    """A sandbox denying a live root must not cost it its record."""
    live = _project(tmp_path / "live")
    hidden = tmp_path / "locked" / "root"
    hidden.mkdir(parents=True)
    record_written(hidden, "default", {hidden / "a.md": text_entry(hidden / "a.md", "x\n")})
    kept = written_state_path(hidden)
    hidden.parent.chmod(0)
    try:
        assert loadout.main(["sync", "--root", str(live)]) == 0
    finally:
        hidden.parent.chmod(0o755)

    assert kept.is_file()


def test_a_record_with_no_readable_root_is_left_alone(tmp_path: Path, scratch_state: Path) -> None:
    live = _project(tmp_path / "live")
    records = scratch_state / "loadout" / "written"
    records.mkdir(parents=True)
    corrupt = records / "0000000000000000.json"
    corrupt.write_text("{not json")
    rootless = records / "1111111111111111.json"
    rootless.write_text('{"version": 1, "written": {}}\n')

    assert loadout.main(["sync", "--root", str(live)]) == 0

    assert corrupt.read_text() == "{not json"
    assert rootless.read_text() == '{"version": 1, "written": {}}\n'
