from __future__ import annotations

import os
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import migration_git
from .errors import LoadoutError, UsageError

EVENTS = ("pre-commit", "post-checkout", "post-merge")
HOOK_MODE = 0o755
# Identity lives in this marker, never in the command the hook invokes, so renaming
# the command does not orphan the hooks already on disk. Match the prefix so a v2 resolves.
SENTINEL = b"# loadout Git hook v"


def hook_command(event: str, source: Path, profile: str) -> str:
    relative = shlex.quote(str(source))
    return (
        f'loadout hook-event {event} --root "$repo"/{relative} '
        f'--profile {shlex.quote(profile)} -- "$@"'
    )


def written_by_loadout(path: Path, source: Path | None = None) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        body = path.read_bytes()
    except OSError:
        return False
    if not body.partition(b"\n")[2].startswith(SENTINEL):
        return False
    if source is None:
        return True
    # The --root argument names the source this hook serves, and the rename does not
    # touch it, so this still recognises a hook generated before the command moved.
    return f'--root "$repo"/{shlex.quote(str(source))} '.encode() in body


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


def _repository_storage(repository: Path) -> list[Path]:
    """The common Git directory and every registered worktree root.

    A linked worktree reaches the repository's hooks directory from outside its own
    root, so containment in `repository` alone cannot tell shared from external.
    """
    paths = []
    common = migration_git.git(
        repository, "rev-parse", "--path-format=absolute", "--git-common-dir", check=False
    )
    if common.returncode == 0:
        paths.append((repository / os.fsdecode(common.stdout).strip()).resolve())
    entries = migration_git.git(repository, "worktree", "list", "--porcelain", "-z", check=False)
    if entries.returncode == 0:
        for entry in entries.stdout.split(b"\0"):
            if entry.startswith(b"worktree "):
                paths.append(Path(os.fsdecode(entry.removeprefix(b"worktree "))).resolve())
    return paths


def placement(repository: Path, directory: Path, *, existing: bool = True) -> str:
    """Why the directory is or is not ours alone: "local", "shared" or "external".

    install refuses the last two alike, because the hook body hard-codes this worktree's
    source path. uninstall must tell them apart: it never wrote outside the repository,
    but a repo-local directory shared between worktrees still holds loadout's own hook.
    """
    inside = directory.is_relative_to(repository) and directory.resolve().is_relative_to(repository)
    if not inside and not (
        existing
        and any(directory.resolve().is_relative_to(p) for p in _repository_storage(repository))
    ):
        return "external"
    if not all(
        not path.is_symlink() and (not path.exists() or path.is_dir())
        for path in (directory, *directory.parents)
        if path.is_relative_to(repository)
    ):
        return "external"
    if existing:
        entries = migration_git.git(repository, "worktree", "list", "--porcelain", "-z").stdout
        for entry in entries.split(b"\0"):
            if not entry.startswith(b"worktree "):
                continue
            worktree = Path(os.fsdecode(entry.removeprefix(b"worktree ")))
            if worktree.resolve() == repository.resolve():
                continue
            if not worktree.is_dir() or hooks_directory(worktree).resolve() == directory.resolve():
                return "shared"
    return "local"


def local_directory(repository: Path, directory: Path, *, existing: bool = True) -> bool:
    return placement(repository, directory, existing=existing) == "local"


@dataclass(frozen=True)
class Hook:
    event: str
    path: Path
    content: bytes
    status: str
    command: str
    owned: bool = False


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
    where = placement(repository, directory, existing=initialize is None)
    hooks = []
    for event in EVENTS if regenerate else EVENTS[:1]:
        path = directory / event
        content = hook_content(event, source, profile)
        owned = written_by_loadout(path, source)
        if where != "local":
            status = where
        elif path.exists() or path.is_symlink():
            if not owned:
                status = "occupied"
            elif path.read_bytes() == content and path.stat().st_mode & stat.S_IXUSR:
                status = "managed"
            else:
                status = "stale"
        else:
            status = "install"
        hooks.append(
            Hook(event, path, content, status, hook_command(event, source, profile), owned)
        )
    return HookPlan(repository, source, directory, tuple(hooks), profile)


def show_hooks(preview: dict[str, Any]) -> None:
    for hook in preview["hooks"]:
        print(f"Git hook {hook['event']}: {hook['status']} ({hook['path']})")
        if hook["status"] not in {"install", "managed"}:
            print(
                "Preserved. Integrate in your existing hook, after setting "
                "repo=$(git rev-parse --show-toplevel):\n  " + hook["command"]
            )


def _require_source(root: Path) -> None:
    if not (root / "loadout.toml").is_file() and not (root / "loadout/config.toml").is_file():
        raise LoadoutError(f"no Loadout source at {root}; select its root with --root")


def install_hooks(
    root: Path, *, regenerate: bool = False, profile: str = "default", dry_run: bool = False
) -> int:
    _require_source(root)
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


_STATE = {
    "install": "not installed",
    "managed": "installed by loadout",
    "stale": "installed by loadout, out of date",
    "occupied": "present, not loadout's",
}


def status_hooks(root: Path, *, profile: str = "default") -> int:
    _require_source(root)
    plan = plan_hooks(root, regenerate=True, profile=profile)
    where = plan.hooks[0].status
    print(f"Hooks directory: {plan.directory}")
    if where == "external":
        print("  outside this repository; loadout did not write here")
    elif where == "shared":
        print("  shared with other worktrees of this repository")
    for hook in plan.hooks:
        if hook.status in {"shared", "external"}:
            state = "installed by loadout" if hook.owned else _STATE["install"]
            if not hook.owned and (hook.path.exists() or hook.path.is_symlink()):
                state = _STATE["occupied"]
        else:
            state = _STATE[hook.status]
        print(f"  {hook.event}: {state}")
    return 0


def _confirm_removal(directory: Path, yes: bool) -> bool:
    if yes:
        return True
    try:
        response = input(f"remove the loadout Git hooks in {directory}? [y/N] ")
    except (EOFError, OSError) as error:
        raise UsageError(
            "git-hooks uninstall requires --yes when input is not interactive"
        ) from error
    return response.strip().lower() in {"y", "yes"}


def uninstall_hooks(root: Path, *, profile: str = "default", yes: bool = False) -> int:
    _require_source(root)
    plan = plan_hooks(root, regenerate=True, profile=profile)
    if plan.hooks[0].status == "external":
        raise LoadoutError(
            f"hooks directory is outside this repository: {plan.directory}; "
            "loadout did not write there"
        )
    removable = [hook for hook in plan.hooks if hook.owned]
    if not removable:
        print("no loadout Git hooks installed; no changes made")
        return 0
    for hook in removable:
        print(f"remove {hook.event} ({hook.path})")
    if plan.hooks[0].status == "shared":
        print("This hooks directory is shared with other worktrees of this repository.")
    if not _confirm_removal(plan.directory, yes):
        print("declined; no changes made")
        return 0
    for hook in removable:
        if not written_by_loadout(hook.path, plan.source):
            raise LoadoutError(f"hook changed during uninstall; preserved: {hook.path}")
        hook.path.unlink()
    print(f"removed {len(removable)} Git hook(s)")
    return 0
