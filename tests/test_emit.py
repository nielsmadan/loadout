from __future__ import annotations

import stat
from pathlib import Path

from loadout.emit import atomic_write, check_all, render_all, write_all


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


def test_atomic_write_preserves_a_symlink(tmp_path: Path) -> None:
    """A destination is often a symlink into the user's config repo (the
    pre-loadout deployment mechanism). Writing through it must never replace
    the link with a plain file — see fix round 2."""
    real_file = tmp_path / "config-repo" / "CLAUDE.md"
    real_file.parent.mkdir(parents=True)
    real_file.write_text("old\n")

    link = Path.home() / ".claude" / "CLAUDE.md"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real_file)

    atomic_write(link, "new\n")

    assert link.is_symlink()
    assert real_file.read_text() == "new\n"


def test_write_all_creates_every_target(root: Path) -> None:
    # 14, not 11: instructions.claude-autonomous declares profile = "autonomous"
    # in the fixture manifest, so it is excluded under the default (no) profile,
    # leaving 10 in-repo outputs plus the 4 destinations those 10 fan out to
    # (1 for claude/CLAUDE.md, 3 for global/AGENTS.md).
    written = write_all(root)
    assert len(written) == 12
    for path in written:
        assert path.is_file()


def test_check_all_is_clean_right_after_write(root: Path) -> None:
    write_all(root)
    assert check_all(root) == []


def test_check_all_reports_a_modified_file(root: Path) -> None:
    write_all(root)
    victim = root / "out" / "shared.md"
    victim.write_text("tampered\n")
    drift = check_all(root)
    assert [p for p, _, _ in drift] == [victim]


def test_check_all_reports_a_missing_file_as_empty_actual(root: Path) -> None:
    write_all(root)
    (root / "out" / "shared.md").unlink()
    drift = check_all(root)
    assert len(drift) == 1
    _, actual, expected = drift[0]
    assert actual == ""
    assert expected != ""


def test_render_all_keys_are_absolute_paths_under_root(root: Path) -> None:
    # Destinations are the exception: they live under the (test-isolated) home
    # directory, not under root, by design.
    for path in render_all(root):
        assert path.is_absolute()
        assert root in path.parents or Path.home() in path.parents
