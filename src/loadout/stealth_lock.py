from __future__ import annotations

import fcntl
import hashlib
import os
import signal
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager, nullcontext
from pathlib import Path

from . import machine
from .destinations import resolve_destination
from .errors import LoadoutError
from .harnesses import GLOBAL_PRESET

LOCK_TIMEOUT_SECONDS = 30.0
HARNESS_LOCK_WAIT_SECONDS = 15.0
HARNESS_LOCK_REFRESH_SECONDS = 5.0
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


def _holder_pid(lock_path: Path) -> int | None:
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def _held_message(lock_path: Path, what: str) -> str:
    pid = _holder_pid(lock_path)
    if pid is None:
        return f"another process holds {lock_path}; wait for it to finish, then run sync again"
    return f"another loadout process (pid {pid}) is syncing {what}; wait for it to finish"


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
                    raise LoadoutError(_held_message(lock_path, what)) from None
                time.sleep(_POLL_SECONDS)
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def _lock_file(kind: str, path: Path) -> Path:
    return machine.state_dir() / "stealth-locks" / f"{kind}-{path_key(path)}.lock"


@contextmanager
def stealth_lock(folder: Path) -> Iterator[None]:
    with _flocked(_lock_file("stealth", folder), str(folder)):
        yield


@contextmanager
def file_lock(path: Path) -> Iterator[None]:
    with _flocked(_lock_file("file", path), str(path)):
        yield


def _is_ours(lock_dir: Path, identity: tuple[int, int], mtime_ns: int) -> bool:
    now = os.stat(lock_dir)
    return (now.st_dev, now.st_ino) == identity and now.st_mtime_ns == mtime_ns


def _rmdir_if_ours(lock_dir: Path, identity: tuple[int, int], mtime_ns: int) -> None:
    try:
        if _is_ours(lock_dir, identity, mtime_ns):
            os.rmdir(lock_dir)
    except OSError:
        pass


def _keep_fresh(
    lock_dir: Path, identity: tuple[int, int], seen: list[int], stop: threading.Event
) -> None:
    if hasattr(signal, "pthread_sigmask"):
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    while not stop.wait(HARNESS_LOCK_REFRESH_SECONDS):
        try:
            if not _is_ours(lock_dir, identity, seen[0]):
                return
            os.utime(lock_dir)
            seen[0] = os.stat(lock_dir).st_mtime_ns
        except OSError:
            continue


def _start_without_sigint(thread: threading.Thread) -> None:
    if not hasattr(signal, "pthread_sigmask"):
        thread.start()
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        thread.start()
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def _proper_lockfile(path: Path, harness: str) -> Iterator[None]:
    lock_dir = Path(f"{os.path.abspath(path)}.lock")
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        deadline = time.monotonic() + HARNESS_LOCK_WAIT_SECONDS
        delay = 0.1
        while True:
            try:
                os.mkdir(lock_dir)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise LoadoutError(
                        f"{harness} holds {lock_dir}; close the {harness} session that is "
                        "saving, or wait, then run sync again"
                    ) from None
                time.sleep(max(0.0, min(delay, deadline - time.monotonic())))
                delay *= 2
        made = os.stat(lock_dir)
        identity = (made.st_dev, made.st_ino)
        seen = [made.st_mtime_ns]
        stop = threading.Event()
        refresher = threading.Thread(
            target=_keep_fresh, args=(lock_dir, identity, seen, stop), daemon=True
        )
        try:
            _start_without_sigint(refresher)
            yield
        finally:
            stop.set()
            if refresher.ident is not None:
                refresher.join()
            _rmdir_if_ours(lock_dir, identity, seen[0])


def claude_json_locks(path: Path) -> AbstractContextManager[None]:
    # Claude Code 2.1.317 locks with proper-lockfile: lockfilePath:`${y}.lock`, taken by fs.mkdir.
    return _proper_lockfile(path, "Claude Code")


def pi_settings(environ: Mapping[str, str] | None = None) -> Path | None:
    template = GLOBAL_PRESET["pi"]["plugins"].destination
    assert template is not None
    try:
        settings = resolve_destination(
            template, "pi settings", os.environ if environ is None else environ
        )
    except LoadoutError:
        return None
    return Path(os.path.abspath(settings))


def _is_pi_settings(path: Path, environ: Mapping[str, str]) -> bool:
    return Path(os.path.abspath(path)) == pi_settings(environ)


def locking_harness(path: Path, environ: Mapping[str, str] | None = None) -> str | None:
    if path.name == ".claude.json":
        return "Claude Code"
    if _is_pi_settings(path, os.environ if environ is None else environ):
        return "Pi"
    return None


def harness_lock(
    path: Path, environ: Mapping[str, str] | None = None
) -> AbstractContextManager[None]:
    harness = locking_harness(path, environ)
    return nullcontext() if harness is None else _proper_lockfile(path, harness)
