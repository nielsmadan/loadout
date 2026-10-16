from __future__ import annotations

import shlex
import sys
from pathlib import Path

from .commands import cmd_sync
from .staged import check_staged
from .worktrees import linked_worktree_source


def run_hook(
    event: str, root: Path, profile: str, arguments: list[str], explicit: str | None = None
) -> int:
    """`explicit` is the profile the hook was given, which the retry it suggests repeats;
    the one resolved from the machine config is resolved again by that retry."""
    if event == "pre-commit":
        return check_staged(root, profile)
    if event == "post-checkout" and arguments and arguments[-1] == "0":
        return 0
    if (source := linked_worktree_source(root)) is not None:
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
