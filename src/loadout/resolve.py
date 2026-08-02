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


@lru_cache
def _fragments_root(source: Source) -> Path:
    return (source.path / "global" / "fragments").resolve()


def _fragment_path(source: Source, name: str) -> Path:
    base = _fragments_root(source)
    candidate = (base / f"{name}.md").resolve()
    if candidate != base and base not in candidate.parents:
        raise LoadoutError(f"fragment name escapes its source: {name!r}")
    return candidate


def resolve_fragment(sources: tuple[Source, ...], name: str) -> ResolvedItem:
    usable = [s for s in sources if "instructions" in s.use]

    if "/" in name:
        source_name, _, bare = name.partition("/")
        matched = [s for s in usable if s.name == source_name]
        if not matched:
            known = ", ".join(sorted(s.name for s in usable)) or "(none)"
            raise LoadoutError(
                f"unknown source {source_name!r} in {name!r}; known sources: {known}"
            )
        path = _fragment_path(matched[0], bare)
        if not path.is_file():
            raise LoadoutError(f"fragment not found: {name!r} (looked in {path})")
        return ResolvedItem(name=bare, source=source_name, path=path)

    hits: list[tuple[Source, Path]] = []
    escaped: list[str] = []
    for s in usable:
        try:
            path = _fragment_path(s, name)
            hits.append((s, path))
        except LoadoutError:
            escaped.append(s.name)
    found = [(s, p) for s, p in hits if p.is_file()]
    if not found:
        detail = f" (rejected as escaping its source in: {', '.join(escaped)})" if escaped else ""
        raise LoadoutError(f"fragment not found in any source: {name!r}{detail}")
    if len(found) > 1:
        names = ", ".join(sorted(f"{s.name}/{name}" for s, _ in found))
        raise LoadoutError(
            f"{name!r} is ambiguous across sources: {names}. Qualify it with a source name."
        )
    source, path = found[0]
    return ResolvedItem(name=name, source=source.name, path=path)
