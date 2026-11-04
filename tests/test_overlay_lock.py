from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from loadout import overlay_lock
from loadout.errors import LoadoutError

HOLDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "from loadout.overlay_lock import overlay_lock\n"
    "with overlay_lock(Path(sys.argv[1])):\n"
    "    print('held', flush=True)\n"
    "    sys.stdin.read()\n"
)


def test_a_held_overlay_lock_names_its_holder_then_frees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.5)
    child = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdin is not None and child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "held"
        with pytest.raises(LoadoutError) as raised, overlay_lock.overlay_lock(tmp_path):
            pass
        assert f"pid {child.pid}" in str(raised.value)
    finally:
        child.stdin.close()
        try:
            child.wait(10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        child.stdout.close()
    ran = []
    with overlay_lock.overlay_lock(tmp_path):
        ran.append(os.getpid())
    assert ran == [os.getpid()]


def test_different_folders_lock_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    ran = []
    with overlay_lock.overlay_lock(tmp_path / "a"), overlay_lock.overlay_lock(tmp_path / "b"):
        ran.append("body")
    assert ran == ["body"]


def test_file_lock_keys_by_resolved_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    assert overlay_lock.path_key(link) == overlay_lock.path_key(target)
    with (
        overlay_lock.file_lock(target),
        pytest.raises(LoadoutError, match="another loadout"),
        overlay_lock.file_lock(link),
    ):
        pass


def test_claude_json_locks_leave_a_foreign_lock_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "CLAUDE_LOCK_WAIT_SECONDS", 0.3)
    path = tmp_path / ".claude.json"
    foreign = Path(f"{path}.lock")
    foreign.mkdir()
    with (
        pytest.raises(LoadoutError, match="Claude Code holds"),
        overlay_lock.claude_json_locks(path),
    ):
        pass
    assert foreign.is_dir()


def test_claude_json_locks_remove_only_their_own_directory(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        assert lock_dir.is_dir()
    assert not lock_dir.exists()
    with pytest.raises(RuntimeError), overlay_lock.claude_json_locks(path):
        assert lock_dir.is_dir()
        raise RuntimeError("boom")
    assert not lock_dir.exists()


def test_claude_json_locks_wait_is_bounded_by_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClock:
        def __init__(self) -> None:
            self.now = 0.0
            self.sleeps: list[float] = []

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)
            self.now += seconds

    clock = FakeClock()
    monkeypatch.setattr(overlay_lock, "time", clock)
    monkeypatch.setattr(overlay_lock, "CLAUDE_LOCK_WAIT_SECONDS", 15.0)
    path = tmp_path / ".claude.json"
    Path(f"{path}.lock").mkdir()
    with (
        pytest.raises(LoadoutError, match="Claude Code holds"),
        overlay_lock.claude_json_locks(path),
    ):
        pass
    assert clock.now == pytest.approx(15.0)
    assert clock.sleeps[:3] == [0.1, 0.2, 0.4]
    assert clock.sleeps[-1] == pytest.approx(15.0 - 12.7)


def test_claude_json_locks_put_the_directory_beside_the_symlink(tmp_path: Path) -> None:
    target_dir = tmp_path / "dotfiles"
    target_dir.mkdir()
    target = target_dir / "claude.json"
    target.write_text("{}")
    link = tmp_path / ".claude.json"
    link.symlink_to(target)
    with overlay_lock.claude_json_locks(link):
        assert Path(f"{link}.lock").is_dir()
        assert not Path(f"{target}.lock").exists()
    assert not Path(f"{link}.lock").exists()


def test_claude_json_locks_require_the_file_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    path = tmp_path / ".claude.json"
    with (
        overlay_lock.file_lock(path),
        pytest.raises(LoadoutError, match="another loadout"),
        overlay_lock.claude_json_locks(path),
    ):
        pass
    assert list(tmp_path.glob("*.lock")) == []


def test_claude_json_locks_leave_a_replaced_directory_alone(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        lock_dir.rmdir()
        lock_dir.mkdir()
        (lock_dir / "marker").write_text("claude")
    assert (lock_dir / "marker").read_text() == "claude"


def test_a_vanished_lock_directory_does_not_mask_the_body_error(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    with pytest.raises(RuntimeError, match="boom"), overlay_lock.claude_json_locks(path):
        Path(f"{path}.lock").rmdir()
        raise RuntimeError("boom")
