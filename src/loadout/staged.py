from __future__ import annotations

import contextlib
import json
import os
import posixpath
import subprocess
import sys
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git_runner
from .emit import RenderContext, refuse_unregistered_global
from .errors import LoadoutError
from .machine import is_registered, registration
from .manifest import MANIFEST_NAME
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR
from .sources import ARTIFACT_TYPES
from .worktrees import linked_worktree_source


def git(root: Path, *arguments: str, data: bytes | None = None) -> bytes:
    # The index git was given is the one inspected, so it alone of git's variables stays.
    result = git_runner.run(root, *arguments, data=data, keep_index=True)
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

    A thread writes every request while this one reads the replies: writing them all
    first from one thread deadlocks once git's output pipe and its input pipe are both
    full, and one request per reply waited a round trip per file (review 3, batch 3.2)."""
    destination.mkdir()
    files = {name: entry for name, entry in entries.items() if entry.mode != "160000"}
    process = git_runner.start(root, "cat-file", "--batch", keep_index=True)
    assert process.stdin is not None and process.stdout is not None
    requests = process.stdin

    def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ValueError):
            for entry in files.values():
                requests.write(entry.oid.encode() + b"\n")
            requests.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    links = []
    completed = False
    try:
        for name, entry in files.items():
            target = destination / name
            match process.stdout.readline().split():
                case [oid, b"blob", size] if oid.decode() == entry.oid:
                    content = process.stdout.read(int(size))
                case _:
                    content = None
            # A short read is a blob git stopped sending part way (review 4, batch 2.11).
            if content is None or len(content) != int(size) or process.stdout.read(1) != b"\n":
                raise LoadoutError(
                    f"cannot read staged blob: {name}; repair the supplied Git index"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == "120000":
                links.append((target, content))
            elif entry.mode in {"100644", "100755"}:
                target.write_bytes(content)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
            else:
                raise LoadoutError(f"unsupported staged mode {entry.mode}: {name}")
        completed = True
    finally:
        process.stdout.close()
        # Only a read that failed part way stops git: once every reply is read, the writer
        # may not have closed git's input yet, and git exits on its own when it does.
        if not completed and process.poll() is None:
            process.kill()
        writer.join()
        code = process.wait()
        with contextlib.suppress(BrokenPipeError):
            requests.close()
    if code:
        raise LoadoutError(f"git cat-file exited {code}; repair the supplied Git index")
    for path, content in links:
        path.symlink_to(os.fsdecode(content))


def _read_blob(root: Path, entry: Entry | None) -> bytes:
    return git(root, "cat-file", "blob", entry.oid) if entry is not None else b""


def _render_inputs(root: Path, relative: Path, entries: dict[str, Entry]) -> dict[str, Entry]:
    """The entries a render of this source can read. A project reads its `loadout/`
    folder and the files at its root. A global source reads its manifest and profiles,
    and of each source they declare, the files in its folder and the folders of the
    artifact types it uses (review 3, batch 3.2: a source at the repository root wrote
    the whole repository out). Nothing else of the repository is written out."""
    base = PurePosixPath(relative.as_posix())
    project = str(base / PROJECT_DIR / PROJECT_CONFIG_NAME) in entries
    own = {str(base)}
    # Folders read whole: a project's `loadout/`, and each source's artifact folders.
    whole = [base / PROJECT_DIR] if project else []
    # A dual-scope root renders its global sources too, wherever they sit (review 4, 2.3).
    declared = (
        _declared(root, base, entries)
        if not project or str(base / MANIFEST_NAME) in entries
        else _Declared([], [])
    )
    sources = declared.sources
    files = {str(path) for path in declared.files}
    own.update(str(folder) for folder, _ in sources)
    whole += [folder / kind for folder, use in sources for kind in sorted(use)]
    prefixes = tuple(_prefix(folder) for folder in whole)

    def wanted(name: str) -> bool:
        parent = name.rpartition("/")[0] or "."
        return parent in own or name.startswith(prefixes) or name in files

    selected = {name: entry for name, entry in entries.items() if wanted(name)}
    read = [
        *((folder, True) for folder in whole),
        *((folder, False) for folder, _ in sources),
        *((path, True) for path in declared.files),
    ]
    return _through_links(root, entries, selected, read)


def _through_links(
    root: Path,
    entries: dict[str, Entry],
    selected: dict[str, Entry],
    folders: list[tuple[PurePosixPath, bool]],
) -> dict[str, Entry]:
    """`selected` with what its links inside the repository name: a source folder, an
    artifact folder or a file that is a link reads its target (review 4, batch 2.3).
    Each folder is read as `selected` reads it, whole or its files alone."""
    links = {
        PurePosixPath(name): entry for name, entry in entries.items() if entry.mode == "120000"
    }
    if not links:
        return selected
    result = dict(selected)
    read = [
        *folders,
        *((PurePosixPath(name), True) for name in selected if entries[name].mode == "120000"),
    ]
    seen: set[tuple[PurePosixPath, bool]] = set()
    while read:
        path, whole = read.pop()
        for link, entry in links.items():
            if link != path and link not in path.parents:
                continue
            # One leaving the repository names no entry; the render refuses it itself.
            target = posixpath.normpath(str(link.parent / os.fsdecode(_read_blob(root, entry))))
            mapped = PurePosixPath(target) / path.relative_to(link)
            result[str(link)] = entry
            if (mapped, whole) in seen:
                continue
            seen.add((mapped, whole))
            reached = {
                name: found for name, found in entries.items() if _under(name, mapped, whole)
            }
            result.update(reached)
            read += [
                (mapped, whole),
                *((PurePosixPath(n), True) for n, e in reached.items() if e.mode == "120000"),
            ]
    return result


def _under(name: str, folder: PurePosixPath, whole: bool) -> bool:
    """Whether `name` is `folder` itself, or in it: anywhere when read whole, else as a
    file of its own."""
    if name == str(folder):
        return True
    return name.startswith(_prefix(folder)) if whole else name.rpartition("/")[0] == str(folder)


def _and_above(name: str) -> list[str]:
    """`name` and each folder above it, as index entries spell them."""
    parts = name.split("/")
    return ["/".join(parts[:end]) for end in range(len(parts), 0, -1)]


def _prefix(folder: PurePosixPath) -> str:
    return "" if str(folder) == "." else f"{folder}/"


def _ownership_inputs(relative: Path, entries: dict[str, Entry]) -> dict[str, Entry]:
    """The entries the HEAD pass reads: the manifest and the profiles beside it, which
    `extends` cannot leave, and the project config. It renders nothing, so nothing
    else is written out."""
    base = PurePosixPath(relative.as_posix())
    config = str(base / PROJECT_DIR / PROJECT_CONFIG_NAME)

    def wanted(name: str) -> bool:
        parent = name.rpartition("/")[0] or "."
        return name == config or (parent == str(base) and name.endswith(".toml"))

    return {name: entry for name, entry in entries.items() if wanted(name)}


@dataclass(frozen=True)
class _Declared:
    """What the manifest and the profiles beside it name for a render to read: each
    source with the artifact types it uses, and each permission target's `base` file."""

    sources: list[tuple[PurePosixPath, frozenset[str]]]
    files: list[PurePosixPath]


def _declared(root: Path, base: PurePosixPath, entries: dict[str, Entry]) -> _Declared:
    """One that does not parse declares nothing here; the render says why."""
    profiles = [
        name
        for name in entries
        if (name.rpartition("/")[0] or ".") == str(base) and name.endswith(".toml")
    ]
    found: list[tuple[PurePosixPath, frozenset[str]]] = []
    files: list[PurePosixPath] = []
    for name in profiles:
        try:
            document = tomllib.loads(_read_blob(root, entries[name]).decode())
        except (UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        targets = document.get("permissions", {})
        # A base file outside every source folder (review 4, batch 2.4).
        files += [
            PurePosixPath(posixpath.normpath(str(base / block["base"])))
            for block in (targets.values() if isinstance(targets, dict) else [])
            if isinstance(block, dict) and isinstance(block.get("base"), str)
        ]
        sources = document.get("source", [])
        for entry in sources if isinstance(sources, list) else []:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                continue
            raw_use = entry.get("use")
            use = (
                frozenset(str(kind) for kind in raw_use)
                if isinstance(raw_use, list)
                else ARTIFACT_TYPES
            )
            found.append((PurePosixPath(posixpath.normpath(str(base / entry["path"]))), use))
    return _Declared(found, files)


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
    # Case-folded: on a case-insensitive disk the snapshot reads `Loadout.toml` as the manifest.
    # A linked worktree of the registered source renders only into the sandbox below.
    # One read decides every question about the registration (review 4, batch 2.10).
    found = registration()
    worktree = linked_worktree_source(root, found) is not None
    manifest = str(relative / MANIFEST_NAME).casefold()
    if not worktree:
        refuse_unregistered_global(
            # The index alone, never the disk: a commit that removes the manifest, even
            # one left on disk by `git rm --cached`, is what fixes it.
            root,
            RenderContext(registration=lambda: found),
            held=any(name.casefold() == manifest for name in staged),
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
        _snapshot(repository, index_tree, _render_inputs(repository, relative, staged))
        _snapshot(repository, head_tree, _ownership_inputs(relative, head))
        owned = set(_inspect(head_tree, check, head))
        owned.update(_inspect(index_tree, check, staged))
        # A staged path is generated when it, or a folder above it, is an output. As
        # strings: a Path and its parents per entry was most of a large index's check.
        generated = {posixpath.normpath(output) for output in owned}
        changed = [
            path
            for path, entry in staged.items()
            if head.get(path) != entry and not generated.isdisjoint(_and_above(path))
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
