from __future__ import annotations

import shlex
import sys
from pathlib import Path

from .commands import cmd_sync
from .staged import check_staged


def run_hook(event: str, root: Path, profile: str, arguments: list[str]) -> int:
    if event == "pre-commit":
        return check_staged(root, profile)
    if event == "post-checkout" and arguments and arguments[-1] == "0":
        return 0
    try:
        result = cmd_sync(root, profile)
    except Exception as error:
        print(f"loadout: {error}", file=sys.stderr)
        result = 3
    if result:
        command = shlex.join(["loadout", "sync", "--root", str(root), "--profile", profile])
        print(
            f"Git {event.removeprefix('post-')} already completed; Loadout regeneration failed. "
            f"Preserve output edits, reconcile the reported conflict, then run: {command}",
            file=sys.stderr,
        )
    return result
