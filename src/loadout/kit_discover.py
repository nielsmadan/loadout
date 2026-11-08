"""The names a bare `kit add` can offer.

`kit list` shows the kits a project names; a fresh project needs to *find* one.
This is the source `list` cannot currently surface: everything resolution could
find — the project's vendored copies first (they shadow source offers), then
every source the machine's global manifest declares, then the kits loadout bundles.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundled_kits import BUNDLED, BUNDLED_KITS
from .errors import LoadoutError
from .kits import VENDORED, kit_sources, source_kit_names, vendored_names
from .resolve import SourceKind


@dataclass(frozen=True)
class AvailableKit:
    name: str
    source: str
    kind: SourceKind = "source"


def available_kits(root: Path | None) -> tuple[AvailableKit, ...]:
    """Every kit a bare `kit add` can offer, vendored copies first.

    A name with a vendored copy shadows the same name in a source; a source that
    offers a name is listed under that source and deduplicated.
    """
    options: list[AvailableKit] = []
    seen: set[str] = set()
    for name in vendored_names(root) if root is not None else ():
        options.append(AvailableKit(name, VENDORED, "vendored"))
        seen.add(name)
    try:
        offering = kit_sources()
    except LoadoutError:
        # No machine config means only vendored copies can resolve.
        offering = ()
    for source in offering:
        for name in source_kit_names(source):
            if name in seen:
                continue
            options.append(AvailableKit(name, source.name))
            seen.add(name)
    options += [AvailableKit(name, BUNDLED, "bundled") for name in BUNDLED_KITS if name not in seen]
    return tuple(options)
