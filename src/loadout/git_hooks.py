from __future__ import annotations

import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import migration_git
from .errors import LoadoutError

EVENTS = ("pre-commit", "post-checkout", "post-merge")
HOOK_MODE = 0o755


def hook_command(event: str, source: Path, profile: str) -> str:
    relative = shlex.quote(str(source))
    return (
        f'loadout git-hooks run {event} --root "$repo"/{relative} '
        f'--profile {shlex.quote(profile)} -- "$@"'
    )


def hook_content(event: str, source: Path, profile: str) -> bytes:
    missing = "loadout is missing from PATH; install the loadout package and retry."
    if event != "pre-commit":
        missing = f"Git {event.removeprefix('post-')} already completed; " + missing
    return (
        "#!/bin/sh\n# loadout Git hook v1\n"
        "repo=$(git rev-parse --show-toplevel) || exit $?\n"
        f"command -v loadout >/dev/null 2>&1 || {{ printf '%s\\n' {shlex.quote(missing)} >&2; exit 127; }}\n"
        f"exec {hook_command(event, source, profile)}\n"
    ).encode()


def hooks_directory(repository: Path, *, existing: bool = True) -> Path:
    if existing:
        raw = migration_git.git(
            repository, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
        ).stdout
    else:
        configured = migration_git.git(
            repository, "config", "--path", "--get", "core.hooksPath", check=False
        )
        if configured.returncode not in {0, 1}:
            raise LoadoutError("could not inspect effective core.hooksPath")
        raw = configured.stdout if configured.returncode == 0 else b".git/hooks"
    return (repository / os.fsdecode(raw).strip()).absolute()


def local_directory(repository: Path, directory: Path, *, existing: bool = True) -> bool:
    if not directory.is_relative_to(repository) or not directory.resolve().is_relative_to(
        repository
    ):
        return False
    if not all(
        not path.is_symlink() and (not path.exists() or path.is_dir())
        for path in (directory, *directory.parents)
        if path.is_relative_to(repository)
    ):
        return False
    if existing:
        entries = migration_git.git(repository, "worktree", "list", "--porcelain", "-z").stdout
        for entry in entries.split(b"\0"):
            if not entry.startswith(b"worktree "):
                continue
            worktree = Path(os.fsdecode(entry.removeprefix(b"worktree ")))
            if worktree.resolve() == repository.resolve():
                continue
            if not worktree.is_dir() or hooks_directory(worktree).resolve() == directory.resolve():
                return False
    return True


@dataclass(frozen=True)
class Hook:
    event: str
    path: Path
    content: bytes
    status: str
    command: str


@dataclass(frozen=True)
class HookPlan:
    repository: Path
    source: Path
    directory: Path
    hooks: tuple[Hook, ...]
    profile: str = "default"

    def preview(self) -> dict[str, Any]:
        return {
            "repository": str(self.repository),
            "source": str(self.source),
            "directory": str(self.directory),
            "profile": self.profile,
            "hooks": [
                {"event": h.event, "path": str(h.path), "status": h.status, "command": h.command}
                for h in self.hooks
            ],
        }


def plan_hooks(
    root: Path,
    *,
    regenerate: bool = False,
    profile: str = "default",
    initialize: Path | None = None,
) -> HookPlan:
    root = root.resolve()
    probe = root
    while not probe.is_dir():
        probe = probe.parent
    repository = (
        initialize.resolve()
        if initialize is not None
        else Path(
            os.fsdecode(migration_git.git(probe, "rev-parse", "--show-toplevel").stdout).strip()
        ).resolve()
    )
    source = root.relative_to(repository)
    directory = hooks_directory(repository, existing=initialize is None)
    local = local_directory(repository, directory, existing=initialize is None)
    hooks = []
    for event in EVENTS if regenerate else EVENTS[:1]:
        path = directory / event
        content = hook_content(event, source, profile)
        if not local:
            status = "shared-or-external"
        elif path.exists() or path.is_symlink():
            status = (
                "managed"
                if not path.is_symlink()
                and path.is_file()
                and path.read_bytes() == content
                and path.stat().st_mode & stat.S_IXUSR
                else "occupied"
            )
        else:
            status = "install"
        hooks.append(Hook(event, path, content, status, hook_command(event, source, profile)))
    return HookPlan(repository, source, directory, tuple(hooks), profile)


def show_hooks(preview: dict[str, Any]) -> None:
    for hook in preview["hooks"]:
        print(f"Git hook {hook['event']}: {hook['status']} ({hook['path']})")
        if hook["status"] not in {"install", "managed"}:
            print(
                "Preserved. Integrate in your existing hook, after setting "
                "repo=$(git rev-parse --show-toplevel):\n  " + hook["command"]
            )


def install_hooks(
    root: Path, *, regenerate: bool = False, profile: str = "default", dry_run: bool = False
) -> int:
    if not (root / "loadout.toml").is_file() and not (root / "loadout/config.toml").is_file():
        raise LoadoutError(f"no Loadout source at {root}; select its root with --root")
    plan = plan_hooks(root, regenerate=regenerate, profile=profile)
    show_hooks(plan.preview())
    if dry_run:
        return 0
    for hook in plan.hooks:
        if hook.status != "install":
            continue
        if hooks_directory(plan.repository) != plan.directory or not local_directory(
            plan.repository, plan.directory
        ):
            raise LoadoutError("effective hook directory changed; preview installation again")
        plan.directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(hook.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
        except FileExistsError as error:
            raise LoadoutError(
                f"hook appeared during installation; preserved: {hook.path}"
            ) from error
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(hook.content)
            handle.flush()
            os.fchmod(handle.fileno(), 0o755)
            os.fsync(handle.fileno())
    return 0
