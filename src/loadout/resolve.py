from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .errors import LoadoutError
from .sources import Source


@dataclass(frozen=True)
class ResolvedItem:
    """A named item, the source it came from, and where it lives on disk."""

    name: str
    source: str
    path: Path


@dataclass(frozen=True)
class Slice:
    """Where one kind of artifact lives inside a source.

    A slice is a directory, and the directory is the namespace: two slices may
    hold the same name without colliding, because nothing resolves across them.

    `directory` makes the *item* a tree rather than a document — a template is a
    bundle of slices, so it has no suffix and no single file to read.
    """

    use: str
    subdir: str
    suffix: str
    directory: bool = False


INSTRUCTIONS = Slice(use="instructions", subdir="instructions", suffix=".md")
SETTINGS = Slice(use="settings", subdir="settings", suffix=".json")


def json_slice(name: str) -> Slice:
    """The slice a name refers to, by convention: the directory is the namespace.

    A fragment name says nothing about which directory holds it, so a resolver
    needs the slice as well as the name — two slices may hold the same name
    without colliding.
    """
    return SETTINGS if name == SETTINGS.use else Slice(use=name, subdir=name, suffix=".json")


@lru_cache
def _slice_root(source: Source, subdir: str) -> Path:
    return (source.path / subdir).resolve()


def _item_path(source: Source, name: str, kind: Slice) -> Path:
    base = _slice_root(source, kind.subdir)
    candidate = (base / f"{name}{kind.suffix}").resolve()
    if candidate != base and base not in candidate.parents:
        raise LoadoutError(f"{kind.use} name escapes its source: {name!r}")
    return candidate


def _present(path: Path, kind: Slice) -> bool:
    return path.is_dir() if kind.directory else path.is_file()


def resolve_item(sources: tuple[Source, ...], name: str, kind: Slice) -> ResolvedItem:
    usable = [s for s in sources if kind.use in s.use]

    if "/" in name:
        source_name, _, bare = name.partition("/")
        matched = [s for s in usable if s.name == source_name]
        if not matched:
            known = ", ".join(sorted(s.name for s in usable)) or "(none)"
            raise LoadoutError(
                f"unknown source {source_name!r} in {name!r}; known sources: {known}"
            )
        path = _item_path(matched[0], bare, kind)
        if not _present(path, kind):
            raise LoadoutError(f"{kind.use} not found: {name!r} (looked in {path})")
        return ResolvedItem(name=bare, source=source_name, path=path)

    hits: list[tuple[Source, Path]] = []
    escaped: list[str] = []
    for s in usable:
        try:
            path = _item_path(s, name, kind)
            hits.append((s, path))
        except LoadoutError:
            escaped.append(s.name)
    found = [(s, p) for s, p in hits if _present(p, kind)]
    if not found:
        detail = f" (rejected as escaping its source in: {', '.join(escaped)})" if escaped else ""
        raise LoadoutError(f"{kind.use} not found in any source: {name!r}{detail}")
    if len(found) > 1:
        names = ", ".join(sorted(f"{s.name}/{name}" for s, _ in found))
        raise LoadoutError(
            f"{name!r} is ambiguous across sources: {names}. Qualify it with a source name."
        )
    source, path = found[0]
    return ResolvedItem(name=name, source=source.name, path=path)


def resolve_fragment(sources: tuple[Source, ...], name: str) -> ResolvedItem:
    return resolve_item(sources, name, INSTRUCTIONS)
