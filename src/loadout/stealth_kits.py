from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundled_kits import BUNDLED_KITS
from .composition import fragment_paths
from .emit import (
    ProjectKits,
    global_rules,
    global_servers,
    layered_rules,
    layered_servers,
    layered_skill_trees,
    skill_trees,
)
from .errors import LoadoutError
from .kit_catalog import kit_name, load_catalogs
from .kits import KitNotFound, NoGlobalSource, kit_sources, resolve_upstream_kit
from .machine import machine_config_path
from .manifest import Manifest
from .permissions.rules import Rules
from .servers import Server
from .skills import Skill
from .sources import Source

NO_KITS = ProjectKits()

_UNRENDERED = ("hooks", "plugins")


@dataclass(frozen=True)
class LinkedKit:
    name: str
    source: str
    path: Path


def parse_kit_names(raw: object, path: Path) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(name, str) for name in raw):
        raise LoadoutError(f"{path}: [stealth] kits must be a list of kit names")
    for name in raw:
        try:
            kit_name(name)
        except LoadoutError as error:
            raise LoadoutError(f"{path}: [stealth] kits: {error}") from None
    duplicates = sorted({name for name in raw if raw.count(name) > 1})
    if duplicates:
        raise LoadoutError(f"{path}: [stealth] kits lists {', '.join(duplicates)} more than once")
    return tuple(raw)


def _not_found(name: str, path: Path, offered: tuple[str, ...], *, configured: bool) -> str:
    names = sorted(set(offered) | set(BUNDLED_KITS))
    sentences = [f"{path}: kit {name!r} not found."]
    if not configured:
        sentences.append(
            "No global source is configured, so only bundled kits resolve; "
            "run `loadout init global` to link kits from a source."
        )
    sentences.append(f"Available: {', '.join(names)}.")
    return " ".join(sentences)


def _offering() -> tuple[Source, ...] | None:
    return kit_sources() if machine_config_path().exists() else None


def _link(name: str, path: Path, offering: tuple[Source, ...] | None) -> LinkedKit:
    try:
        found = resolve_upstream_kit(name, offering=offering)
    except KitNotFound as error:
        raise LoadoutError(_not_found(name, path, error.offered, configured=True)) from None
    except NoGlobalSource:
        raise LoadoutError(_not_found(name, path, (), configured=False)) from None
    except LoadoutError as error:
        raise LoadoutError(f"{path}: kit {name!r}: {error}") from None
    single = ProjectKits((found.path,), load_catalogs((found.path,)))
    parts = [part for slice_name in _UNRENDERED for part in single.contributions(slice_name)]
    if parts:
        raise LoadoutError(
            f"{path}: kit {name!r} brings {', '.join(str(p) for p in parts)}, which would "
            "need a destination the team's config does not merge, so a stealth source "
            "cannot link it"
        )
    return LinkedKit(name, found.source, found.path)


def link_kits(names: tuple[str, ...], path: Path) -> tuple[LinkedKit, ...]:
    if not names:
        return ()
    offering = _offering()
    return tuple(_link(name, path, offering) for name in names)


def link_each(
    names: tuple[str, ...], path: Path
) -> tuple[tuple[str, LinkedKit | LoadoutError], ...]:
    if not names:
        return ()
    offering = _offering()
    results: list[tuple[str, LinkedKit | LoadoutError]] = []
    for name in names:
        try:
            results.append((name, _link(name, path, offering)))
        except LoadoutError as error:
            results.append((name, error))
    return tuple(results)


def resolve_stealth_kits(names: tuple[str, ...], path: Path) -> ProjectKits:
    paths = tuple(kit.path for kit in link_kits(names, path))
    return ProjectKits(paths, load_catalogs(paths)) if paths else NO_KITS


def stealth_instruction_sources(
    manifest: Manifest, kits: ProjectKits, harness: str, *, kit_parts: bool
) -> tuple[Path, ...]:
    target = next((t for t in manifest.targets if t.name == harness), None)
    own = fragment_paths(target, manifest) if target is not None else ()
    return (*own, *(kits.contributions("instructions") if kit_parts else ()))


def stealth_rules(manifest: Manifest, kits: ProjectKits) -> Rules:
    return layered_rules(kits, (global_rules(manifest),))


def stealth_servers(manifest: Manifest, kits: ProjectKits) -> dict[str, Server]:
    return layered_servers(kits, global_servers(manifest))


def stealth_skill_trees(manifest: Manifest, kits: ProjectKits) -> tuple[Skill, ...]:
    return layered_skill_trees(kits, skill_trees(manifest))
