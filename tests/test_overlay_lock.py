from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

from loadout import atomic, overlay_lock
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


FOREIGN_HOLDER = (
    "import fcntl, os, sys\n"
    "fd = os.open(sys.argv[1], os.O_RDWR)\n"
    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
    "print('held', flush=True)\n"
    "sys.stdin.read()\n"
)


def test_a_lock_held_by_another_program_names_the_lock_file_not_a_stale_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.3)
    with overlay_lock.overlay_lock(tmp_path):
        pass
    lock_path = overlay_lock._lock_file("overlay", tmp_path)
    lock_path.write_text(str(os.getpid()))
    child = subprocess.Popen(
        [sys.executable, "-c", FOREIGN_HOLDER, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert child.stdin is not None and child.stdout is not None
    try:
        assert child.stdout.readline().strip() == "held"
        lock_path.write_text("999999999")
        with pytest.raises(LoadoutError) as raised, overlay_lock.overlay_lock(tmp_path):
            pass
        assert str(raised.value) == (
            f"another process holds {lock_path}; wait for it to finish, then run sync again"
        )
    finally:
        child.stdin.close()
        try:
            child.wait(10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        child.stdout.close()


def test_a_released_lock_file_carries_no_pid(tmp_path: Path) -> None:
    with overlay_lock.overlay_lock(tmp_path):
        held = overlay_lock._lock_file("overlay", tmp_path).read_text()
    assert held == str(os.getpid())
    assert overlay_lock._lock_file("overlay", tmp_path).read_text() == ""


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
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 0.3)
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
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 15.0)
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


PROPER_LOCKFILE_STALE_SECONDS = 10.0


class Interrupted(BaseException):
    pass


def _wait_for_mtime_change(lock_dir: Path, before: int) -> int:
    deadline = time.monotonic() + 5
    while lock_dir.stat().st_mtime_ns == before and time.monotonic() < deadline:
        time.sleep(0.01)
    return lock_dir.stat().st_mtime_ns


def test_a_held_harness_lock_refreshes_its_own_directorys_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_REFRESH_SECONDS", 0.05)
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        made = lock_dir.stat().st_mtime_ns
        first = _wait_for_mtime_change(lock_dir, made)
        second = _wait_for_mtime_change(lock_dir, first)
    assert made < first < second


def test_the_refresh_runs_twice_inside_proper_lockfiles_stale_window() -> None:
    assert 0 < overlay_lock.HARNESS_LOCK_REFRESH_SECONDS <= PROPER_LOCKFILE_STALE_SECONDS / 2


def test_the_refresher_leaves_a_replaced_lock_directory_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_REFRESH_SECONDS", 0.05)
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        lock_dir.rmdir()
        lock_dir.mkdir()
        (lock_dir / "marker").write_text("claude")
        os.utime(lock_dir, (1_000_000, 1_000_000))
        time.sleep(0.5)
        seen = lock_dir.stat().st_mtime
    assert seen == 1_000_000
    assert (lock_dir / "marker").read_text() == "claude"


def test_the_refresher_leaves_a_directory_re_made_with_its_inode_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_REFRESH_SECONDS", 0.05)
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        made = lock_dir.stat().st_ino
        os.utime(lock_dir, (1_000_000, 1_000_000))
        time.sleep(0.5)
        seen = lock_dir.stat()
    assert (seen.st_ino, seen.st_mtime) == (made, 1_000_000)


def test_release_leaves_a_directory_re_made_with_its_inode_in_place(tmp_path: Path) -> None:
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    with overlay_lock.claude_json_locks(path):
        made = lock_dir.stat().st_ino
        os.utime(lock_dir, (1_000_000, 1_000_000))
    assert lock_dir.stat().st_ino == made
    assert lock_dir.stat().st_mtime == 1_000_000


def test_the_refresher_keeps_refreshing_after_a_transient_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_REFRESH_SECONDS", 0.05)
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    real_utime = os.utime
    calls: list[str] = []

    def flaky_utime(*args: object, **kwargs: object) -> None:
        calls.append("utime")
        if len(calls) == 1:
            raise OSError(5, "Input/output error")
        real_utime(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "utime", flaky_utime)
    with overlay_lock.claude_json_locks(path):
        made = lock_dir.stat().st_mtime_ns
        refreshed = _wait_for_mtime_change(lock_dir, made)
    assert len(calls) >= 2
    assert refreshed > made


def test_a_harness_lock_released_during_the_wait_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(overlay_lock, "HARNESS_LOCK_WAIT_SECONDS", 5.0)
    path = tmp_path / ".claude.json"
    lock_dir = Path(f"{path}.lock")
    lock_dir.mkdir()
    release = threading.Timer(0.3, lock_dir.rmdir)
    release.start()
    held: list[bool] = []
    started = time.monotonic()
    try:
        with overlay_lock.claude_json_locks(path):
            held.append(lock_dir.is_dir())
    finally:
        release.cancel()
    assert held == [True]
    assert 0.3 <= time.monotonic() - started < 5.0


def test_ctrl_c_during_a_locked_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".claude.json"
    path.write_text("{}")
    real_mkstemp = tempfile.mkstemp

    def interrupted_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        made = real_mkstemp(*args, **kwargs)  # type: ignore[call-overload]
        os.kill(os.getpid(), signal.SIGINT)
        time.sleep(0.2)
        return made

    def interrupt(signum: int, frame: object) -> None:
        raise Interrupted

    monkeypatch.setattr(atomic.tempfile, "mkstemp", interrupted_mkstemp)
    previous = signal.signal(signal.SIGINT, interrupt)
    try:
        with pytest.raises(Interrupted), overlay_lock.claude_json_locks(path):
            time.sleep(0.1)
            atomic.replace_atomically(path, lambda handle: handle.write(b'{"a": 1}'))
    finally:
        signal.signal(signal.SIGINT, previous)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [".claude.json"]
    assert path.read_text() == "{}"


def test_pis_lock_is_taken_for_its_global_settings_json_not_for_claudes(tmp_path: Path) -> None:
    agent_dir = tmp_path / "pi-agent"
    environ = {"HOME": str(tmp_path / "home"), "PI_CODING_AGENT_DIR": str(agent_dir)}
    assert overlay_lock.locking_harness(agent_dir / "settings.json", environ) == "Pi"
    assert overlay_lock.locking_harness(tmp_path / ".claude.json", environ) == "Claude Code"
    assert overlay_lock.locking_harness(tmp_path / "home/.claude/settings.json", environ) is None
