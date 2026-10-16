from __future__ import annotations

import json
import os
import posixpath
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import git_runner
from .emit import RenderContext, refuse_unregistered_manifest
from .errors import LoadoutError
from .git_tree import (
    Entry,
    and_above,
    ownership_inputs,
    render_inputs,
    tree_entries,
    write_snapshot,
)
from .machine import fixed, is_registered, registration
from .manifest import MANIFEST_NAME
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR
from .worktrees import linked_worktree_source


def git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    return git_runner.output(root, *arguments, data=data)


@dataclass(frozen=True)
class _Check:
    """What every snapshot of one `check --staged` run is inspected against."""

    original: Path
    relative: Path
    profile: str
    registered: bool


def _inspect(snapshot: Path, check: _Check, entries: dict[str, Entry]) -> tuple[str, ...]:
    render = snapshot.name == "index"
    original = check.original
    request = {
        "snapshot": str(snapshot),
        "original": str(original),
        "relative": str(check.relative),
        "profile": check.profile,
        "render": render,
        "environment": dict(os.environ),
        "gitlinks": [path for path, entry in entries.items() if entry.mode == "160000"],
        "registered": check.registered,
    }
    result = subprocess.run(
        [sys.executable, "-I", "-m", "loadout.staged_render"],
        input=json.dumps(request).encode(),
        capture_output=True,
        check=False,
        cwd=snapshot.parent,
    )
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        detail = detail.replace(str(snapshot), str(original))
        raise LoadoutError(
            f"{'staged' if render else 'HEAD'} dependency snapshot is incomplete or invalid: {detail}\n"
            "Stage every required source/config/support file and its deletions; vendor external "
            "dependencies inside this repository and use relative source paths. A gitignored "
            "personal file may be absent, but a required input must be staged; never stage secrets."
        )
    return tuple(json.loads(result.stdout))


def check_staged(root: Path, profile: str = "default") -> int:
    root = root.absolute()
    probe = git_runner.nearest_folder(root)
    # Checked, not `toplevel`: a commit's check says exactly why git could not answer.
    repository = Path(os.fsdecode(git(probe, "rev-parse", "--show-toplevel")).strip()).resolve()
    if not root.resolve().is_relative_to(repository):
        raise LoadoutError(f"source root escapes Git repository: {root}")
    relative = root.resolve().relative_to(repository)
    supplied = os.environ.get("GIT_INDEX_FILE")
    if supplied and not Path(supplied).is_file():
        raise LoadoutError(
            f"supplied GIT_INDEX_FILE is missing or unreadable: {supplied}; restore that index or "
            "unset GIT_INDEX_FILE explicitly before retrying; the live index was not used"
        )
    staged = tree_entries(repository, keep_index=True)
    head_ref = git(repository, "rev-parse", "--revs-only", "HEAD").decode().strip()
    head = tree_entries(repository, head_ref, keep_index=True) if head_ref else {}
    # One read decides every question about the registration (review 4, batch 2.10).
    found = registration()
    # A linked worktree of the registered source renders only into the sandbox below.
    worktree = linked_worktree_source(root, found) is not None
    # Case-folded: on a case-insensitive disk the snapshot reads `Loadout.toml` as the manifest.
    manifest = str(relative / MANIFEST_NAME).casefold()
    held = [name for name in staged if name.casefold() == manifest]
    if not worktree:
        refuse_unregistered_manifest(
            # The index alone, never the disk: a commit that removes the manifest, even
            # one left on disk by `git rm --cached`, is what fixes it.
            root,
            held=bool(held),
            context=RenderContext(registration=fixed(found)),
            staged_as=held[0] if held else None,
        )
    registered = worktree or is_registered(root, found.source)
    check = _Check(repository, relative, profile, registered)
    configs = {str(relative / MANIFEST_NAME), str(relative / PROJECT_DIR / PROJECT_CONFIG_NAME)}
    if not configs.intersection(staged.keys() | head.keys()):
        raise LoadoutError(
            f"no loadout config in HEAD or the supplied index at {root}; "
            "stage the source config or select its root with --root"
        )
    with tempfile.TemporaryDirectory(prefix="loadout-staged-") as directory:
        scratch = Path(directory).resolve()
        index_tree, head_tree = scratch / "index", scratch / "head"
        write_snapshot(
            repository,
            index_tree,
            render_inputs(repository, relative, staged, keep_index=True),
            keep_index=True,
        )
        write_snapshot(repository, head_tree, ownership_inputs(relative, head), keep_index=True)
        owned = set(_inspect(head_tree, check, head))
        owned.update(_inspect(index_tree, check, staged))
        # A staged path is generated when it, or a folder above it, is an output. As
        # strings: a Path and its parents per entry was most of a large index's check.
        generated = {posixpath.normpath(output) for output in owned}
        changed = [
            path
            for path, entry in staged.items()
            if head.get(path) != entry and not generated.isdisjoint(and_above(path))
        ]
        if changed:
            commands = "\n".join(f"  {path}" for path in sorted(changed))
            raise LoadoutError(
                f"generated files are staged for addition or editing:\n{commands}\n"
                "Edit and stage their loadout sources. Unstage these output edits, or remove "
                "generated paths from the index with git rm --cached -- <path>; keep the local copies."
            )
    print("staged loadout dependency snapshot is valid")
    return 0
