from __future__ import annotations

import contextlib
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .emit import refuse_unregistered_global
from .errors import LoadoutError
from .manifest import MANIFEST_NAME
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR


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
        metadata, raw_name = entry.split(b"\t", 1)
        mode, middle, last = metadata.decode().split()
        name = os.fsdecode(raw_name)
        # Split as text: a Path per entry was most of the check on a large repository.
        parts = name.split("/")
        if name.startswith("/") or ".." in parts or ".git" in parts or "" in parts:
            raise LoadoutError(f"invalid staged path: {name}")
        if not revision and last != "0":
            raise LoadoutError(
                f"unmerged index entry: {name}; resolve and stage the conflict first"
            )
        result[name] = Entry(mode, last if revision else middle)
    return result


def _snapshot(root: Path, destination: Path, entries: dict[str, Entry]) -> None:
    """Write `entries` out as files, streaming their blobs from one `cat-file` run.

    One request at a time, each reply read before the next is sent: writing every
    request first deadlocks once git's output pipe and its input pipe are both full."""
    destination.mkdir()
    files = {name: entry for name, entry in entries.items() if entry.mode != "160000"}
    process = subprocess.Popen(
        ["git", "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
    )
    assert process.stdin is not None and process.stdout is not None
    links = []
    try:
        for name, entry in files.items():
            target = destination / name
            process.stdin.write(entry.oid.encode() + b"\n")
            process.stdin.flush()
            match process.stdout.readline().split():
                case [oid, b"blob", size] if oid.decode() == entry.oid:
                    content = process.stdout.read(int(size))
                case _:
                    raise LoadoutError(
                        f"cannot read staged blob: {name}; repair the supplied Git index"
                    )
            process.stdout.read(1)
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == "120000":
                links.append((target, content))
            elif entry.mode in {"100644", "100755"}:
                target.write_bytes(content)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
            else:
                raise LoadoutError(f"unsupported staged mode {entry.mode}: {name}")
    finally:
        with contextlib.suppress(BrokenPipeError):
            process.stdin.close()
        process.stdout.close()
        process.wait()
    for path, content in links:
        path.symlink_to(os.fsdecode(content))


def _read_blob(root: Path, entry: Entry | None) -> bytes:
    return git(root, "cat-file", "blob", entry.oid) if entry is not None else b""


def _render_inputs(root: Path, relative: Path, entries: dict[str, Entry]) -> dict[str, Entry]:
    """The entries a render of this source can read. A project reads its `loadout/`
    folder and the files at its root; a global source reads its own folder and any
    source it names outside it. Nothing else of the repository is written out."""
    base = PurePosixPath(relative.as_posix())
    project = str(base / PROJECT_DIR / PROJECT_CONFIG_NAME) in entries
    folders = [base / PROJECT_DIR] if project else [base, *_external_sources(root, base, entries)]
    prefixes = tuple("" if str(folder) == "." else f"{folder}/" for folder in folders)
    exact = {str(folder) for folder in folders}

    def wanted(name: str) -> bool:
        if project and (name.rpartition("/")[0] or ".") == str(base):
            return True
        return name.startswith(prefixes) or name in exact

    return {name: entry for name, entry in entries.items() if wanted(name)}


def _ownership_inputs(relative: Path, entries: dict[str, Entry]) -> dict[str, Entry]:
    """The entries the HEAD pass reads: the manifest and the profiles beside it, which
    `extends` cannot leave, and the project config. It renders nothing, so nothing
    else is written out."""
    base = PurePosixPath(relative.as_posix())
    config = str(base / PROJECT_DIR / PROJECT_CONFIG_NAME)

    def wanted(name: str) -> bool:
        path = PurePosixPath(name)
        return name == config or (path.parent == base and path.suffix == ".toml")

    return {name: entry for name, entry in entries.items() if wanted(name)}


def _external_sources(
    root: Path, base: PurePosixPath, entries: dict[str, Entry]
) -> list[PurePosixPath]:
    manifest = _read_blob(root, entries.get(str(base / MANIFEST_NAME)))
    try:
        document = tomllib.loads(manifest.decode())
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return []
    sources = document.get("source", [])
    found = [
        PurePosixPath(posixpath.normpath(str(base / entry["path"])))
        for entry in sources
        if isinstance(sources, list)
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]
    return [path for path in found if not path.is_relative_to(base)]


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
            "dependencies inside this repository and use relative source paths. A gitignored "
            "personal file may be absent, but a required input must be staged; never stage secrets."
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
    refuse_unregistered_global(
        root, held=str(relative / MANIFEST_NAME) in staged.keys() | head.keys()
    )
    configs = {str(relative / MANIFEST_NAME), str(relative / PROJECT_DIR / PROJECT_CONFIG_NAME)}
    if not configs.intersection(staged.keys() | head.keys()):
        raise LoadoutError(
            f"no loadout config in HEAD or the supplied index at {root}; "
            "stage the source config or select its root with --root"
        )
    with tempfile.TemporaryDirectory(prefix="loadout-staged-") as directory:
        scratch = Path(directory).resolve()
        index_tree, head_tree = scratch / "index", scratch / "head"
        _snapshot(repository, index_tree, _render_inputs(repository, relative, staged))
        _snapshot(repository, head_tree, _ownership_inputs(relative, head))
        owned = set(_inspect(head_tree, repository, relative, profile, head))
        owned.update(_inspect(index_tree, repository, relative, profile, staged))
        # A staged path is generated when it, or a folder above it, is an output.
        generated = {Path(output) for output in owned}
        changed = [
            path
            for path, entry in staged.items()
            if head.get(path) != entry
            and not generated.isdisjoint((Path(path), *Path(path).parents))
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
