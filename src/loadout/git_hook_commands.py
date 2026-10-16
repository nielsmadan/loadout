from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

from . import git_runner
from .commands import cmd_sync
from .machine import registration
from .manifest import MANIFEST_NAME, manifest_path
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR, project_config_path
from .staged import check_staged
from .worktrees import linked_worktree_source


def run_hook(
    event: str, root: Path, profile: str, arguments: list[str], explicit: str | None = None
) -> int:
    """`explicit` is the profile the hook was given, which the retry it suggests repeats;
    the one resolved from the machine config is resolved again by that retry."""
    if _nothing_to_protect(event, root):
        # A branch from before adoption, or one without the source folder (review 5, 2.4).
        work = "check" if event == "pre-commit" else "sync"
        print(
            f"loadout: no loadout config at {root} on this branch; nothing to {work}",
            file=sys.stderr,
        )
        return 0
    if event == "pre-commit":
        return check_staged(root, profile)
    if event == "post-checkout" and arguments and arguments[-1] == "0":
        return 0
    if (source := linked_worktree_source(root, registration())) is not None:
        # The source's hooks run in each of its worktrees, which are not the registered folder.
        print(
            f"loadout: {root} is a linked worktree of the registered source {source}; "
            "nothing was synced here",
            file=sys.stderr,
        )
        return 0
    advice = "Preserve output edits, reconcile the reported conflict"
    try:
        result = cmd_sync(root, profile)
    except Exception as error:  # noqa: BLE001 - a failing sync must not crash the git operation
        print(f"loadout: {error}", file=sys.stderr)
        result = 3
        advice = "Fix the reported problem"
    if result:
        profile_arguments = ["--profile", explicit] if explicit else []
        command = shlex.join(["loadout", "sync", "--root", str(root), *profile_arguments])
        print(
            f"Git {event.removeprefix('post-')} already completed; loadout regeneration failed. "
            f"{advice}, then run: {command}",
            file=sys.stderr,
        )
    return result


def _nothing_to_protect(event: str, root: Path) -> bool:
    """No loadout config at `root` on disk and, for a commit, none in the index or HEAD
    either, which a commit deleting the source would still need checked."""
    if manifest_path(root).is_file() or project_config_path(root).is_file():
        return False
    return event != "pre-commit" or not _config_in_git(root)


def _config_in_git(root: Path) -> bool:
    probe = git_runner.nearest_folder(root)
    repository = git_runner.toplevel(probe)
    if repository is None:
        return False
    base = root.resolve() if root.exists() else probe.resolve() / root.relative_to(probe)
    if not base.is_relative_to(repository):
        return False
    relative = base.relative_to(repository).as_posix()
    names = [f"{relative}/{MANIFEST_NAME}", f"{relative}/{PROJECT_DIR}/{PROJECT_CONFIG_NAME}"]
    names = [name.removeprefix("./") for name in names]
    # Case-folded, as a case-insensitive disk reads `Loadout.toml` as the manifest.
    spelled = [f":(icase){name}" for name in names]
    staged = git_runner.run(repository, "ls-files", "--", *spelled, index="given")
    if staged.stdout.strip():
        return True
    # `ls-tree` takes no pathspec magic, so the folder's names are compared here.
    committed = git_runner.run(
        repository, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", relative
    )
    wanted = {name.casefold() for name in names}
    return committed.returncode == 0 and any(
        os.fsdecode(name).casefold() in wanted for name in committed.stdout.split(b"\0")
    )
