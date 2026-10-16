"""A git tree or index as entries, and the part of it a render reads, written out: what
`check --staged` inspects and the guard's committed baseline renders (review 5, batch 3.3).

Reading is apart from selecting: every blob the selection needs is read in one
`cat-file --batch` run first, so the selection itself is a pure function of entries and
contents (review 5, batch 3.1).
"""

from __future__ import annotations

import bisect
import contextlib
import os
import posixpath
import threading
import tomllib
from collections.abc import Generator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import git_runner
from .errors import LoadoutError
from .manifest import MANIFEST_NAME
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR
from .sources import ARTIFACT_TYPES

LINK = "120000"
GITLINK = "160000"
# As the file system gives up on a chain of links (ELOOP).
MAX_HOPS = 40
# As the file system refuses a longer path (ENAMETOOLONG): macOS's PATH_MAX, in bytes.
PATH_MAX = 1024
MAX_DEPTH = 128
# Steps a link walk takes, folders looked up and entries reached, before it stops.
WORK_BUDGET = 1_000_000


@dataclass(frozen=True)
class Entry:
    mode: str
    oid: str


def _index(keep_index: bool) -> git_runner.Index:
    # The index git was given is the one `check --staged` inspects: `commit -a` and
    # `commit -o` hand the hook one of their own (review 5, batch 5.1).
    return "given" if keep_index else None


def parent_of(name: str) -> str:
    """The folder an index entry sits in, `.` at the top."""
    return name.rpartition("/")[0] or "."


def tree_entries(
    root: Path, revision: str | None = None, *, keep_index: bool = False
) -> dict[str, Entry]:
    """The entries of `revision`'s tree, or of the index when there is none; `keep_index`
    reads the index git was given rather than the repository's own."""
    raw = (
        git_runner.output(root, "ls-tree", "-rz", revision, index=_index(keep_index))
        if revision
        else git_runner.output(root, "ls-files", "--stage", "-z", index=_index(keep_index))
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


def blobs(
    root: Path, wanted: Iterable[tuple[str, str]], *, keep_index: bool = False
) -> Generator[bytes]:
    """Each blob's content, for (name, oid) pairs in the order asked, streamed from one
    `cat-file --batch` run; a failure names the entry.

    A thread writes every request while this one reads the replies: writing them all
    first from one thread deadlocks once git's output pipe and its input pipe are both
    full, and one request per reply waited a round trip per file (review 3, batch 3.2)."""
    wanted = list(wanted)
    process = git_runner.start(root, "cat-file", "--batch", index=_index(keep_index))
    assert process.stdin is not None and process.stdout is not None
    requests, replies = process.stdin, process.stdout

    def feed() -> None:
        with contextlib.suppress(BrokenPipeError, ValueError):
            for _, oid in wanted:
                requests.write(oid.encode() + b"\n")
            requests.close()

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    completed = False
    try:
        for name, oid in wanted:
            match replies.readline().split():
                case [found, b"blob", size] if found.decode() == oid:
                    content = replies.read(int(size))
                case _:
                    content = None
            # A short read is a blob git stopped sending part way (review 4, batch 2.11).
            if content is None or len(content) != int(size) or replies.read(1) != b"\n":
                raise LoadoutError(
                    f"cannot read staged blob: {name}; repair the supplied Git index"
                )
            yield content
        completed = True
    finally:
        replies.close()
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


def read_blobs(
    root: Path, wanted: Iterable[tuple[str, str]], *, keep_index: bool = False
) -> dict[str, bytes]:
    """Each distinct blob's content, by its oid, for (name, oid) pairs; a failure names
    the first entry that holds the blob."""
    named: dict[str, str] = {}
    for name, oid in wanted:
        named.setdefault(oid, name)
    pairs = sorted((oid, name) for oid, name in named.items())
    with contextlib.closing(
        blobs(root, ((name, oid) for oid, name in pairs), keep_index=keep_index)
    ) as contents:
        return dict(zip((oid for oid, _ in pairs), contents, strict=True))


def write_snapshot(
    root: Path, destination: Path, entries: Mapping[str, Entry], *, keep_index: bool = False
) -> None:
    """Write `entries` out as files and links; a submodule is no file and stays out."""
    destination.mkdir()
    files = {name: entry for name, entry in entries.items() if entry.mode != GITLINK}
    links = []
    # Closed on any failure, so git is stopped rather than left writing to nobody.
    with contextlib.closing(
        blobs(root, ((name, entry.oid) for name, entry in files.items()), keep_index=keep_index)
    ) as contents:
        for (name, entry), content in zip(files.items(), contents, strict=True):
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if entry.mode == LINK:
                if b"\0" in content:
                    raise LoadoutError(
                        f"{name} is a link whose target holds a NUL byte, which no file "
                        "system can store"
                    )
                links.append((target, content))
            elif entry.mode in {"100644", "100755"}:
                target.write_bytes(content)
                target.chmod(0o755 if entry.mode == "100755" else 0o644)
            else:
                raise LoadoutError(f"unsupported staged mode {entry.mode}: {name}")
    for path, content in links:
        path.symlink_to(os.fsdecode(content))


def render_inputs(
    root: Path, relative: Path, entries: Mapping[str, Entry], *, keep_index: bool = False
) -> dict[str, Entry]:
    """The entries a render of the source at `relative` can read, its profiles and link
    targets read first in one run."""
    base = str(PurePosixPath(relative.as_posix()))
    needed = [
        (name, entry.oid)
        for name, entry in entries.items()
        if entry.mode == LINK or (parent_of(name) == base and name.endswith(".toml"))
    ]
    contents = read_blobs(root, needed, keep_index=keep_index) if needed else {}
    return select_inputs(relative, entries, contents)


def select_inputs(
    relative: Path, entries: Mapping[str, Entry], contents: Mapping[str, bytes]
) -> dict[str, Entry]:
    """The entries a render of this source can read, given each blob it needs by oid. A
    project reads its `loadout/` folder and the files at its root. A global source reads
    its manifest and profiles, and of each source they declare, the files in its folder
    and the folders of the artifact types it uses (review 3, batch 3.2: a source at the
    repository root wrote the whole repository out). Nothing else is written out."""
    base = PurePosixPath(relative.as_posix())
    project = str(base / PROJECT_DIR / PROJECT_CONFIG_NAME) in entries
    own = {str(base)}
    # Folders read whole: a project's `loadout/`, and each source's artifact folders.
    whole = [base / PROJECT_DIR] if project else []
    # A dual-scope root renders its global sources too, wherever they sit (review 4, 2.3).
    declared = (
        _declared(base, entries, contents)
        if not project or str(base / MANIFEST_NAME) in entries
        else _Declared([], [])
    )
    sources = declared.sources
    files = {str(path) for path in declared.files}
    own.update(str(folder) for folder, _ in sources)
    whole += [folder / kind for folder, use in sources for kind in sorted(use)]
    prefixes = tuple(_prefix(str(folder)) for folder in whole)

    def wanted(name: str) -> bool:
        return parent_of(name) in own or name.startswith(prefixes) or name in files

    selected = {name: entry for name, entry in entries.items() if wanted(name)}
    targets = _link_targets(entries, contents)
    # A project's render reads no file through a link among its root files.
    unread = str(base) if project and base not in {folder for folder, _ in sources} else None
    read = [
        *((str(folder), True) for folder in whole),
        *((str(folder), False) for folder, _ in sources),
        *((str(path), True) for path in declared.files),
        *((name, True) for name in selected if name in targets and parent_of(name) != unread),
    ]
    return _through_links(entries, selected, read, targets)


def _link_targets(entries: Mapping[str, Entry], contents: Mapping[str, bytes]) -> dict[str, str]:
    """Where each link entry points, as an entry name; one leaving the repository names
    no entry, and the render refuses it on its own terms."""
    return {
        name: posixpath.normpath(posixpath.join(parent_of(name), os.fsdecode(contents[entry.oid])))
        for name, entry in entries.items()
        if entry.mode == LINK and entry.oid in contents
    }


def _through_links(
    entries: Mapping[str, Entry],
    selected: dict[str, Entry],
    paths: list[tuple[str, bool]],
    targets: Mapping[str, str],
    *,
    budget: int = WORK_BUDGET,
) -> dict[str, Entry]:
    """`selected` with what the links on its read `paths` name inside the repository: a
    source folder, an artifact folder or a file that is a link reads its target (review
    4, batch 2.3). Each folder is read as `selected` reads it, whole or its files alone.
    Only the read path and the folders above it are looked up. A link reached through
    itself, past `MAX_HOPS`, or to a path past `PATH_MAX` or `MAX_DEPTH`, is left
    unresolved as the file system leaves it (review 5, 2.5), and so is what remains once
    `budget` is spent (review 6, batch 1.3)."""
    if not targets:
        return selected
    names = sorted(entries)
    folders = {folder for link in targets if _resolvable(link) for folder in _folders_above(link)}
    result = dict(selected)
    read = [(path, whole, 0) for path, whole in paths if _resolvable(path)]
    seen: set[tuple[str, bool]] = set()
    spent = 0
    while read and spent < budget:
        path, whole, hops = read.pop()
        links, steps = _links_at_or_above(path, targets, folders)
        spent += steps
        for link in links:
            result[link] = entries[link]
            mapped = _join(targets[link], path[len(link) :].lstrip("/"))
            if hops >= MAX_HOPS or not _resolvable(mapped):
                continue
            if mapped == link or mapped.startswith(link + "/") or (mapped, whole) in seen:
                continue
            seen.add((mapped, whole))
            reached = _under(names, mapped, whole)
            spent += len(reached)
            result.update({name: entries[name] for name in reached})
            read += [
                (mapped, whole, hops + 1),
                *((name, True, hops + 1) for name in reached if name in targets),
            ]
    return result


def _resolvable(path: str) -> bool:
    return len(os.fsencode(path)) <= PATH_MAX and path.count("/") < MAX_DEPTH


def _folders_above(name: str) -> list[str]:
    return [name[:end] for end, character in enumerate(name) if character == "/"]


def _links_at_or_above(
    path: str, targets: Mapping[str, str], folders: set[str]
) -> tuple[list[str], int]:
    """The links at `path` or a folder above it, deepest first, and how many folders were
    looked up: walked down from the top, it stops at the first folder holding no link."""
    found = []
    steps = 0
    end = path.find("/")
    while True:
        steps += 1
        prefix = path if end == -1 else path[:end]
        if prefix in targets:
            found.append(prefix)
        if end == -1 or prefix not in folders:
            return found[::-1], steps
        end = path.find("/", end + 1)


def _join(folder: str, rest: str) -> str:
    if not rest:
        return folder
    return rest if folder == "." else f"{folder}/{rest}"


def _under(names: list[str], folder: str, whole: bool) -> list[str]:
    """The sorted `names` that are `folder` itself, or in it: anywhere when read whole,
    else as a file of its own. Found by bisection, never by a scan of every entry."""
    if folder == ".":
        return [name for name in names if whole or "/" not in name]
    found = [folder] if _contains(names, folder) else []
    prefix = folder + "/"
    start = bisect.bisect_left(names, prefix)
    for name in names[start:]:
        if not name.startswith(prefix):
            break
        if whole or "/" not in name[len(prefix) :]:
            found.append(name)
    return found


def _contains(names: list[str], name: str) -> bool:
    index = bisect.bisect_left(names, name)
    return index < len(names) and names[index] == name


def and_above(name: str) -> list[str]:
    """`name` and each folder above it, as index entries spell them."""
    parts = name.split("/")
    return ["/".join(parts[:end]) for end in range(len(parts), 0, -1)]


def _prefix(folder: str) -> str:
    return "" if folder == "." else f"{folder}/"


def ownership_inputs(relative: Path, entries: Mapping[str, Entry]) -> dict[str, Entry]:
    """The entries the HEAD pass reads: the manifest and the profiles beside it, which
    `extends` cannot leave, and the project config. It renders nothing, so nothing
    else is written out."""
    base = str(PurePosixPath(relative.as_posix()))
    config = str(PurePosixPath(base) / PROJECT_DIR / PROJECT_CONFIG_NAME)

    def wanted(name: str) -> bool:
        return name == config or (parent_of(name) == base and name.endswith(".toml"))

    return {name: entry for name, entry in entries.items() if wanted(name)}


@dataclass(frozen=True)
class _Declared:
    """What the manifest and the profiles beside it name for a render to read: each
    source with the artifact types it uses, and each permission target's `base` file."""

    sources: list[tuple[PurePosixPath, frozenset[str]]]
    files: list[PurePosixPath]


def _declared(
    base: PurePosixPath, entries: Mapping[str, Entry], contents: Mapping[str, bytes]
) -> _Declared:
    """One that does not parse declares nothing here; the render says why."""
    profiles = [name for name in entries if parent_of(name) == str(base) and name.endswith(".toml")]
    found: list[tuple[PurePosixPath, frozenset[str]]] = []
    files: list[PurePosixPath] = []
    for name in profiles:
        try:
            document = tomllib.loads(contents.get(entries[name].oid, b"").decode())
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
