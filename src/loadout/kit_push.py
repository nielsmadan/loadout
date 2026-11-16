"""Pushing a vendored kit's edits back to the source it was vendored from.

A push is a fast-forward and nothing else: the source must still hold exactly what the
copy was vendored from, which the recorded hash proves. When both sides moved, nothing
is merged; the push is held with the reason, as `kit sync` refuses (ADR 0057).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .atomic import read_file
from .bundled_kits import BUNDLED
from .errors import LoadoutError
from .kit_catalog import load_catalog
from .kit_vendor import apply_changes
from .kits import kit_files, record_hash, tree_hash, upstream_or_none, vendored_path
from .output import FrozenFile
from .permissions.rules import parse_rules
from .project import load_project_config, project_config_path
from .servers import parse_servers

Changes = tuple[tuple[Path, FrozenFile | None], ...]


@dataclass(frozen=True)
class Push:
    """Edits to send to the source: each source path's new content, or None to delete it,
    with what each path held when planned. `also` names the source's other kits a changed
    shared part changes too."""

    name: str
    local: Path
    upstream: Path
    source: str
    changes: Changes
    before: Changes
    copy: str
    also: tuple[str, ...] = ()


class SourceMoved(LoadoutError):
    """The source changed since the copy was vendored, so a push would overwrite it."""


@dataclass(frozen=True)
class Held:
    """Edits that cannot be pushed, and why. `upstream` is set when the source moved too,
    so the caller can show how the copy and the source differ."""

    name: str
    reason: str
    upstream: Path | None = None


@dataclass(frozen=True)
class Level:
    """A copy that already equals its source, so there is nothing to push, only a hash
    to record."""

    name: str


def plan_push(root: Path, name: str, *, start: str | None = None) -> Push | Held | Level:
    """`start` is the source's hash before this run pushed anything, so a kit sharing a
    part another push just changed is not mistaken for one whose source moved."""
    found = upstream_or_none(name)
    if found is None:
        return Held(name, "its source does not resolve here")
    try:
        return _plan(root, name, found.path, found.source, start)
    except SourceMoved as error:
        return Held(name, str(error), found.path)
    except (LoadoutError, OSError) as error:
        return Held(name, str(error))


def _plan(root: Path, name: str, upstream: Path, source: str, start: str | None) -> Push | Level:
    local = vendored_path(root, name)
    copy = tree_hash(local)
    if local.is_file() == upstream.is_file() and tree_hash(upstream) == copy:
        return Level(name)
    if source == BUNDLED:
        raise LoadoutError(
            "it was vendored from a kit bundled with loadout, which a push cannot change"
        )
    if local.is_file() != upstream.is_file():
        raise LoadoutError("the copy and its source are not the same kind of kit")
    recorded = load_project_config(project_config_path(root)).vendored_hash(name)
    if (tree_hash(upstream) if start is None else start) != recorded:
        raise SourceMoved("its source changed too since the copy was vendored")
    changes, also = (
        _catalog_changes(local, upstream) if local.is_file() else _tree_changes(local, upstream)
    )
    before = tuple((path, read_file(path)) for path, _ in changes)
    return Push(name, local, upstream, source, changes, before, copy, also)


def _differs(path: Path, content: FrozenFile | None) -> bool:
    return read_file(path) != content


def _tree_changes(local: Path, upstream: Path) -> tuple[Changes, tuple[str, ...]]:
    if (local / "permissions.toml").is_file():
        parse_rules(local / "permissions.toml")
    if (local / "mcp.toml").is_file():
        parse_servers(local / "mcp.toml")
    ours = kit_files(local)
    writes = ((upstream / relative, read_file(local / relative)) for relative in ours)
    removals = (
        (upstream / relative, None) for relative in kit_files(upstream) if relative not in ours
    )
    changes = tuple(
        (path, content) for path, content in (*writes, *removals) if _differs(path, content)
    )
    return changes, ()


def _catalog_changes(local: Path, upstream: Path) -> tuple[Changes, tuple[str, ...]]:
    """The manifest and every part it lists, mapped onto the source's catalog folder.
    Loading the copy's catalog validates its parts. A file removed from a skill part the
    source already listed is removed there; nothing else in the source is deleted, so a
    part the manifest no longer lists stays for the other kits that may use it."""
    ours = load_catalog(local)
    before = set(load_catalog(upstream).files())

    def there(path: Path) -> Path:
        return upstream if path == local else upstream.parent / path.relative_to(local.parent)

    writes = tuple((there(path), read_file(path)) for path in ours.files())
    for path, content in writes:
        if path not in before and path.exists() and _differs(path, content):
            raise LoadoutError(
                f"pushing would replace {path}, which the copy did not list when it was "
                f"vendored and which differs from the copy's"
            )
    written = {path for path, _ in writes}
    removals = tuple(
        (path, None)
        for _, part in ours.parts
        if part.is_dir()
        for path in (there(part) / relative for relative in kit_files(there(part)))
        if path in before and path not in written
    )
    changes = tuple(
        (path, content) for path, content in (*writes, *removals) if _differs(path, content)
    )
    return changes, _also_changed(upstream, {path for path, _ in changes})


def _also_changed(upstream: Path, changed: set[Path]) -> tuple[str, ...]:
    """The source's other catalog kits listing a part a changed path is, or is inside."""
    names: list[str] = []
    for manifest in sorted(upstream.parent.glob("*.toml")):
        if manifest == upstream:
            continue
        try:
            parts = tuple(part for _, part in load_catalog(manifest).parts)
        except LoadoutError:
            continue
        if any(path == part or path.is_relative_to(part) for part in parts for path in changed):
            names.append(manifest.stem)
    return tuple(names)


def apply_push(root: Path, push: Push) -> None:
    """Write the push into the source, then record each of the project's vendored kits
    that now matches its source: the pushed kit and any kit sharing a pushed part.
    Refused, with nothing written, when the copy or the source changed since the plan."""
    if tree_hash(push.local) != push.copy or any(
        read_file(path) != content for path, content in push.before
    ):
        raise LoadoutError(
            f"{push.name}: the copy or its source changed while waiting; nothing was "
            f"pushed, so sync again"
        )
    apply_changes(
        dict(push.changes),
        dict(push.before),
        f"push of {push.name} failed; the source is as it was",
    )
    record_matching(root)


def record_matching(root: Path) -> tuple[str, ...]:
    """Record the hash of each vendored kit whose copy equals its source and whose
    recorded hash says otherwise, and name them."""
    config = load_project_config(project_config_path(root))
    recorded: list[str] = []
    for name in config.kits:
        local = vendored_path(root, name)
        found = upstream_or_none(name) if local.exists() else None
        if found is None or local.is_file() != found.path.is_file():
            continue
        copy = tree_hash(local)
        level = tree_hash(found.path) == copy
        if level and copy != config.vendored_hash(name):
            record_hash(root, name, copy)
            recorded.append(name)
    return tuple(recorded)
