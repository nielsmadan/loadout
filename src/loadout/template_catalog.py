from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .artifacts import _no_symlinks, _regular_file
from .errors import LoadoutError
from .permissions.rules import parse_rules
from .servers import parse_servers
from .skills import SKILL_DOCUMENT, Skill, _excluded

CATEGORIES = {"instructions": ".md", "permissions": ".toml", "mcp": ".toml", "skills": ""}
_NAME = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def template_name(name: str) -> None:
    parts = name.split("/")
    if name.count("/") > 1 or any(not _NAME.fullmatch(part) for part in parts):
        raise LoadoutError(f"invalid template name: {name!r}; use a name or source/name")


@dataclass(frozen=True)
class Catalog:
    path: Path
    parts: tuple[tuple[str, Path], ...]
    content: tuple[Path, ...]

    def paths(self, category: str) -> tuple[Path, ...]:
        return tuple(path for kind, path in self.parts if kind == category)

    def files(self) -> tuple[Path, ...]:
        return self.content

    def skills(self) -> tuple[Skill, ...]:
        return tuple(
            Skill(
                path.name,
                path / SKILL_DOCUMENT,
                tuple(
                    p.relative_to(path)
                    for p in self.content
                    if p.is_relative_to(path) and p != path / SKILL_DOCUMENT
                ),
            )
            for path in self.paths("skills")
        )


def _validate_part(target: Path, category: str) -> tuple[Path, ...]:
    _no_symlinks(target)
    if not target.exists():
        raise LoadoutError(f"missing {category} part: {target}")
    if category == "skills":
        if not target.is_dir() or not (target / SKILL_DOCUMENT).is_file():
            raise LoadoutError(f"{target}: skill requires a directory with SKILL.md")
        files = []
        for item in target.rglob("*"):
            _no_symlinks(item)
            if not item.is_dir():
                _regular_file(item)
                if not _excluded(item, item.relative_to(target)):
                    files.append(item)
        return tuple(sorted(files))
    _regular_file(target)
    if category == "permissions":
        parse_rules(target)
    elif category == "mcp":
        parse_servers(target)
    else:
        try:
            target.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise LoadoutError(f"{target}: instructions must be UTF-8") from error
    return (target,)


def load_catalog(path: Path) -> Catalog:
    return _load_catalog(path, {})


def load_catalogs(paths: Iterable[Path]) -> dict[Path, Catalog]:
    validated: dict[tuple[str, Path], tuple[Path, ...]] = {}
    return {path: _load_catalog(path, validated) for path in dict.fromkeys(paths) if path.is_file()}


def _load_catalog(path: Path, validated: dict[tuple[str, Path], tuple[Path, ...]]) -> Catalog:
    _no_symlinks(path)
    _regular_file(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise LoadoutError(f"{path}: invalid template manifest: {error}") from error
    if unknown := set(data) - CATEGORIES.keys():
        raise LoadoutError(f"{path}: unknown template categories: {', '.join(sorted(unknown))}")
    parts: list[tuple[str, Path]] = []
    content = {path}
    for category, names in data.items():
        if not isinstance(names, list) or any(
            not isinstance(n, str) or not _NAME.fullmatch(n) for n in names
        ):
            raise LoadoutError(f"{path}: {category} must be a list of catalog names, not paths")
        if len(names) != len(set(names)):
            raise LoadoutError(f"{path}: duplicate {category} reference")
        for name in names:
            target = path.parent / category / (name + CATEGORIES[category])
            key = (category, target)
            if key not in validated:
                validated[key] = _validate_part(target, category)
            content.update(validated[key])
            parts.append((category, target))
    return Catalog(path, tuple(parts), tuple(sorted(content)))


def contribution_paths(
    templates: tuple[Path, ...], category: str, *, catalogs: Mapping[Path, Catalog] | None = None
) -> tuple[Path, ...]:
    loaded = load_catalogs(templates) if catalogs is None else catalogs
    paths: list[Path] = []
    legacy = {
        "instructions": "instructions.md",
        "permissions": "permissions.toml",
        "mcp": "mcp.toml",
        "skills": "skills",
    }
    for template in templates:
        contributed = (
            loaded[template].paths(category)
            if template in loaded
            else (template / legacy[category],)
        )
        for path in contributed:
            if path.exists():
                paths.append(path)
    return tuple(dict.fromkeys(paths)) if category == "instructions" else tuple(paths)
