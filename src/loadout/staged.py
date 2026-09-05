from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError


def git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    environment = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    if environment.get("GIT_INDEX_FILE"):
        environment["GIT_INDEX_FILE"] = str(Path(environment["GIT_INDEX_FILE"]).absolute())
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=data,
        capture_output=True,
        env=environment,
        check=False,
    )
    if result.returncode:
        raise LoadoutError(
            f"Git {' '.join(arguments[:2])} failed: {result.stderr.decode(errors='replace').strip()}"
        )
    return result.stdout


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str


def _entries(root: Path, revision: str | None = None) -> dict[str, Entry]:
    raw = (
        git(root, "ls-tree", "-rz", revision)
        if revision
        else git(root, "ls-files", "--stage", "-z")
    )
    result = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, middle, last = metadata.decode().split()
        path = Path(os.fsdecode(name))
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise LoadoutError(f"invalid staged path: {path}")
        if not revision and last != "0":
            raise LoadoutError(
                f"unmerged index entry: {path}; resolve and stage the conflict first"
            )
        result[str(path)] = Entry(mode, last if revision else middle)
    return result


def _snapshot(root: Path, destination: Path, entries: dict[str, Entry]) -> None:
    destination.mkdir()
    blobs = [entry.oid for entry in entries.values() if entry.mode != "160000"]
    data = git(root, "cat-file", "--batch", data="".join(oid + "\n" for oid in blobs).encode())
    offset = 0
    links = []
    for name, entry in entries.items():
        target = destination / name
        if entry.mode == "160000":
            continue
        end = data.index(b"\n", offset)
        oid, kind, size = data[offset:end].split()
        if oid.decode() != entry.oid or kind != b"blob":
            raise LoadoutError(f"cannot read staged blob: {name}; repair the supplied Git index")
        offset = end + 1
        content = data[offset : offset + int(size)]
        offset += int(size) + 1
        target.parent.mkdir(parents=True, exist_ok=True)
        if entry.mode == "120000":
            links.append((target, content))
        elif entry.mode in {"100644", "100755"}:
            target.write_bytes(content)
            target.chmod(0o755 if entry.mode == "100755" else 0o644)
        else:
            raise LoadoutError(f"unsupported staged mode {entry.mode}: {name}")
    for path, content in links:
        path.symlink_to(os.fsdecode(content))


def _inspect(
    snapshot: Path, original: Path, relative: Path, profile: str, entries: dict[str, Entry]
) -> tuple[str, ...]:
    render = snapshot.name == "index"
    request = {
        "snapshot": str(snapshot),
        "original": str(original),
        "relative": str(relative),
        "profile": profile,
        "render": render,
        "environment": dict(os.environ),
        "gitlinks": [path for path, entry in entries.items() if entry.mode == "160000"],
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
            "dependencies inside this repository and use relative source paths. Required private "
            "inputs need an optional binding or an explicit public replacement; never stage secrets."
        )
    return tuple(json.loads(result.stdout))


def check_staged(root: Path, profile: str = "default") -> int:
    root = root.absolute()
    repository = Path(os.fsdecode(git(root, "rev-parse", "--show-toplevel")).strip()).resolve()
    if not root.resolve().is_relative_to(repository):
        raise LoadoutError(f"source root escapes Git repository: {root}")
    relative = root.resolve().relative_to(repository)
    supplied = os.environ.get("GIT_INDEX_FILE")
    if supplied and not Path(supplied).is_file():
        raise LoadoutError(
            f"supplied GIT_INDEX_FILE is missing or unreadable: {supplied}; restore that index or "
            "unset GIT_INDEX_FILE explicitly before retrying; the live index was not used"
        )
    staged = _entries(repository)
    head_ref = git(repository, "rev-parse", "--revs-only", "HEAD").decode().strip()
    head = _entries(repository, head_ref) if head_ref else {}
    configs = {str(relative / "loadout.toml"), str(relative / "loadout/config.toml")}
    if not configs.intersection(staged.keys() | head.keys()):
        raise LoadoutError(
            f"no Loadout config in HEAD or the supplied index at {root}; "
            "stage the source config or select its root with --root"
        )
    with tempfile.TemporaryDirectory(prefix="loadout-staged-") as directory:
        scratch = Path(directory).resolve()
        index_tree, head_tree = scratch / "index", scratch / "head"
        _snapshot(repository, index_tree, staged)
        _snapshot(repository, head_tree, head)
        owned = set(_inspect(head_tree, repository, relative, profile, head))
        owned.update(_inspect(index_tree, repository, relative, profile, staged))
        changed = [
            path
            for path, entry in staged.items()
            if head.get(path) != entry
            and any(Path(path).is_relative_to(Path(output)) for output in owned)
        ]
        if changed:
            commands = "\n".join(f"  {path}" for path in sorted(changed))
            raise LoadoutError(
                f"generated files are staged for addition or editing:\n{commands}\n"
                "Edit and stage their Loadout sources. Unstage these output edits, or remove "
                "generated paths from the index with git rm --cached -- <path>; keep the local copies."
            )
    print("staged Loadout dependency snapshot is valid")
    return 0
