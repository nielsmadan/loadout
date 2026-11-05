from __future__ import annotations

import fcntl
import hashlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import machine
from .errors import LoadoutError

LOCK_TIMEOUT_SECONDS = 30.0
CLAUDE_LOCK_WAIT_SECONDS = 15.0
_POLL_SECONDS = 0.1


def path_key(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:16]


def same_path(left: Path, right: Path) -> bool:
    if left == right or path_key(left) == path_key(right):
        return True
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _holder_pid(lock_path: Path) -> str:
    try:
        return lock_path.read_text().strip() or "unknown"
    except OSError:
        return "unknown"


@contextmanager
def _flocked(lock_path: Path, what: str) -> Iterator[None]:
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as error:
        raise LoadoutError(f"{lock_path}: {error}") from None
    try:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LoadoutError(
                        f"another loadout process (pid {_holder_pid(lock_path)}) is syncing "
                        f"{what}; wait for it to finish"
                    ) from None
                time.sleep(_POLL_SECONDS)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)


def _lock_file(kind: str, path: Path) -> Path:
    return machine.state_dir() / "overlay-locks" / f"{kind}-{path_key(path)}.lock"


@contextmanager
def overlay_lock(folder: Path) -> Iterator[None]:
    with _flocked(_lock_file("overlay", folder), str(folder)):
        yield


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    with _flocked(_lock_file("file", path), str(path)):
        yield


def _rmdir_if_ours(lock_dir: Path, identity: tuple[int, int]) -> None:
    try:
        now = os.stat(lock_dir)
        if (now.st_dev, now.st_ino) == identity:
            os.rmdir(lock_dir)
    except OSError:
        pass


@contextmanager
def claude_json_locks(path: Path) -> Iterator[None]:
    lock_dir = Path(f"{os.path.abspath(path)}.lock")
    with file_lock(path):
        # Claude Code 2.1.317 locks with proper-lockfile: lockfilePath:`${y}.lock`, taken by fs.mkdir.
        deadline = time.monotonic() + CLAUDE_LOCK_WAIT_SECONDS
        delay = 0.1
        while True:
            try:
                os.mkdir(lock_dir)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LoadoutError(
                        f"Claude Code holds {lock_dir}; close the Claude session that is "
                        "saving, or wait, then run sync again"
                    ) from None
                time.sleep(max(0.0, min(delay, deadline - time.monotonic())))
                delay *= 2
        made = os.stat(lock_dir)
        try:
            yield
        finally:
            _rmdir_if_ours(lock_dir, (made.st_dev, made.st_ino))
