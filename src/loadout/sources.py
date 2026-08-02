from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError

ARTIFACT_TYPES = frozenset({"instructions", "skills", "mcp", "permissions"})


@dataclass(frozen=True)
class Source:
    """One folder contributing artifacts, and which artifact types to take from it."""

    name: str
    path: Path
    use: frozenset[str]


def parse_sources(entries: list[dict[str, object]], base: Path) -> tuple[Source, ...]:
    sources: list[Source] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise LoadoutError(f"[[source]] entries must be tables, got {entry!r}")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise LoadoutError(f"source entry is missing a name: {entry!r}")
        if name in seen:
            raise LoadoutError(f"duplicate source name: {name!r}")
        seen.add(name)

        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise LoadoutError(f"source {name!r} is missing a path")
        path = Path(raw_path)
        resolved = (path if path.is_absolute() else base / path).resolve()
        if not resolved.is_dir():
            raise LoadoutError(f"source {name!r} directory not found: {resolved}")

        raw_use = entry.get("use")
        if raw_use is None:
            use = ARTIFACT_TYPES
        else:
            if not isinstance(raw_use, list) or not raw_use:
                raise LoadoutError(
                    f"source {name!r}: use must be a non-empty list, got {raw_use!r}"
                )
            unknown = [u for u in raw_use if u not in ARTIFACT_TYPES]
            if unknown:
                known = ", ".join(sorted(ARTIFACT_TYPES))
                raise LoadoutError(
                    f"source {name!r}: unknown use value(s) {unknown!r}; expected any of {known}"
                )
            use = frozenset(str(u) for u in raw_use)

        sources.append(Source(name=name, path=resolved, use=use))
    return tuple(sources)
