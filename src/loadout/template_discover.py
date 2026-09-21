"""The names a bare `template add` can offer.

`template list` shows declared templates; a fresh project needs to *find* one.
This is the source `list` cannot currently surface: everything resolution could
find — the project's vendored copies first (they shadow source offers), then
every source the machine's global manifest declares.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError
from .sources import Source
from .template_catalog import load_catalog
from .templates import (
    PART_DIRECTORIES,
    TEMPLATES_SUBDIR,
    VENDORED,
    declared_sources,
    vendored_names,
)


@dataclass(frozen=True)
class AvailableTemplate:
    name: str
    source: str


def _source_names(source: Source) -> tuple[str, ...]:
    """Templates a source's `templates/` directory can resolve by name."""
    templates_dir = source.path / TEMPLATES_SUBDIR
    if not templates_dir.is_dir():
        return ()
    names: list[str] = []
    for entry in sorted(templates_dir.iterdir()):
        if entry.name in PART_DIRECTORIES and entry.is_dir():
            continue
        if entry.is_file() and entry.suffix == ".toml":
            try:
                load_catalog(entry)
            except LoadoutError:
                continue
            names.append(entry.name.removesuffix(".toml"))
        elif entry.is_dir():
            names.append(entry.name)
    return tuple(dict.fromkeys(names))


def available_templates(root: Path) -> tuple[AvailableTemplate, ...]:
    """Every template a bare `template add` can offer, vendored copies first.

    A name with a vendored copy shadows the same name in a source; a source that
    offers a name is listed under that source and deduplicated.
    """
    options: list[AvailableTemplate] = []
    seen: set[str] = set()
    for name in vendored_names(root):
        options.append(AvailableTemplate(name, VENDORED))
        seen.add(name)
    try:
        declared = declared_sources()
    except LoadoutError:
        # No machine config means only vendored copies can resolve.
        declared = ()
    for source in declared:
        for name in _source_names(source):
            if name in seen:
                continue
            options.append(AvailableTemplate(name, source.name))
            seen.add(name)
    return tuple(options)
