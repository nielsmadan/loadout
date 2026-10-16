"""The names a bare `template add` can offer.

`template list` shows declared templates; a fresh project needs to *find* one.
This is the source `list` cannot currently surface: everything resolution could
find — the project's vendored copies first (they shadow source offers), then
every source the machine's global manifest declares, then the starters loadout ships.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundled_templates import BUNDLED, STARTERS
from .errors import LoadoutError
from .resolve import SourceKind
from .templates import VENDORED, declared_sources, source_template_names, vendored_names


@dataclass(frozen=True)
class AvailableTemplate:
    name: str
    source: str
    kind: SourceKind = "source"


def available_templates(root: Path) -> tuple[AvailableTemplate, ...]:
    """Every template a bare `template add` can offer, vendored copies first.

    A name with a vendored copy shadows the same name in a source; a source that
    offers a name is listed under that source and deduplicated.
    """
    options: list[AvailableTemplate] = []
    seen: set[str] = set()
    for name in vendored_names(root):
        options.append(AvailableTemplate(name, VENDORED, "vendored"))
        seen.add(name)
    try:
        declared = declared_sources()
    except LoadoutError:
        # No machine config means only vendored copies can resolve.
        declared = ()
    for source in declared:
        for name in source_template_names(source):
            if name in seen:
                continue
            options.append(AvailableTemplate(name, source.name))
            seen.add(name)
    options += [
        AvailableTemplate(name, BUNDLED, "bundled") for name in STARTERS if name not in seen
    ]
    return tuple(options)
