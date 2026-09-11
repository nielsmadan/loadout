from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .agents import known_agents
from .errors import LoadoutError

# The seven kinds of agent configuration — see docs/reference/config.md — plus
# templates, which is a bundle of them rather than an eighth kind.
ARTIFACT_TYPES = frozenset(
    {
        "instructions",
        "settings",
        "permissions",
        "hooks",
        "mcp",
        "plugins",
        "defaults",
        "skills",
        "module-config",
        "templates",
    }
)


@dataclass(frozen=True)
class Source:
    """One folder contributing artifacts, and which artifact types to take from it."""

    name: str
    path: Path
    use: frozenset[str]
    overrides: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def override_names(self, category: str) -> frozenset[str]:
        return frozenset(dict(self.overrides).get(category, ()))


def _parse_overrides(
    raw: object, name: str, use: frozenset[str]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    label = f"source {name!r}: overrides"
    if not isinstance(raw, dict):
        raise LoadoutError(f"{label} must be a table")
    parsed: list[tuple[str, tuple[str, ...]]] = []
    for category, names in raw.items():
        if category not in {"skills", "module-config"}:
            raise LoadoutError(f"{label}: unsupported category {category!r}")
        if category not in use:
            raise LoadoutError(f"{label}: {category!r} must participate in use")
        if not isinstance(names, list) or any(not isinstance(n, str) or not n for n in names):
            raise LoadoutError(f"{label}.{category} must be a list of non-empty strings")
        normalized: list[str] = []
        for item in names:
            path = PurePosixPath(item)
            if (
                "\x00" in item
                or path.is_absolute()
                or path == PurePosixPath(".")
                or any(p in {"..", ".git", ".loadout-state"} for p in path.parts)
                or (category == "skills" and len(path.parts) != 1)
                or (
                    category == "module-config"
                    and (len(path.parts) <= 1 or path.parts[0] not in known_agents())
                )
            ):
                raise LoadoutError(f"{label}.{category}: invalid item {item!r}")
            normalized.append(path.as_posix())
        if len(normalized) != len(set(normalized)):
            raise LoadoutError(f"{label}.{category}: duplicate item")
        parsed.append((category, tuple(normalized)))
    return tuple(parsed)


def validate_overrides(
    source: Source, category: str, offered: set[str], previous: set[str]
) -> None:
    for name in sorted(source.override_names(category)):
        if name not in offered:
            raise LoadoutError(f"source {source.name!r}: override {name!r} has no replacing item")
        if name not in previous:
            raise LoadoutError(
                f"source {source.name!r}: override {name!r} has no earlier contender"
            )


def select_overrides(
    category: str, offerings: Iterable[tuple[Source, set[str]]]
) -> dict[str, Source]:
    selected: dict[str, Source] = {}
    for source, offered in offerings:
        validate_overrides(source, category, offered, set(selected))
        for name in sorted(offered):
            if name in selected and name not in source.override_names(category):
                raise LoadoutError(
                    f"{category} item {name!r} is offered by both {selected[name].name!r} "
                    f"and {source.name!r}; declare source.overrides.{category}, rename one, "
                    "or drop it from a source's `use`"
                )
            selected[name] = source
    return selected


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

        sources.append(
            Source(
                name=name,
                path=resolved,
                use=use,
                overrides=_parse_overrides(entry.get("overrides", {}), name, use),
            )
        )
    return tuple(sources)
