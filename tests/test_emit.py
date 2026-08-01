from __future__ import annotations

import stat
from pathlib import Path

import pytest

from loadout.emit import atomic_write, check_all, render_all, write_all

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    fragments = tmp_path / "global" / "fragments"
    fragments.mkdir(parents=True)
    for src in (GOLDEN / "fragments").glob("*.md"):
        (fragments / src.name).write_text(src.read_text())
    return tmp_path


def test_atomic_write_creates_file_with_content(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello\n")
    assert target.read_text() == "hello\n"


def test_atomic_write_leaves_no_temp_files_behind(tmp_path: Path) -> None:
    atomic_write(tmp_path / "out.md", "hello\n")
    assert [p.name for p in tmp_path.iterdir()] == ["out.md"]


def test_atomic_write_replaces_existing_content(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    target.write_text("old\n")
    atomic_write(target, "new\n")
    assert target.read_text() == "new\n"


def test_written_files_are_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "out.md"
    atomic_write(target, "hello\n")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_write_all_creates_every_target(root: Path) -> None:
    written = write_all(root)
    assert len(written) == 3
    for path in written:
        assert path.is_file()


def test_check_all_is_clean_right_after_write(root: Path) -> None:
    write_all(root)
    assert check_all(root) == []


def test_check_all_reports_a_modified_file(root: Path) -> None:
    write_all(root)
    victim = root / "global" / "AGENTS.md"
    victim.write_text("tampered\n")
    drift = check_all(root)
    assert [p for p, _, _ in drift] == [victim]


def test_check_all_reports_a_missing_file_as_empty_actual(root: Path) -> None:
    write_all(root)
    (root / "global" / "AGENTS.md").unlink()
    drift = check_all(root)
    assert len(drift) == 1
    _, actual, expected = drift[0]
    assert actual == ""
    assert expected != ""


def test_render_all_keys_are_absolute_paths_under_root(root: Path) -> None:
    for path in render_all(root):
        assert path.is_absolute()
        assert root in path.parents
