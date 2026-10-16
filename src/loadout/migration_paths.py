from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from .migration_models import Inventory, RootMapping


def entry_path(path: Path) -> Path:
    return path.parent.resolve() / path.name


@dataclass(frozen=True)
class DestinationLayout:
    root: Path
    mappings: tuple[RootMapping, ...]
    home: Path | None = None

    @cached_property
    def _boundaries(self) -> tuple[tuple[Path, Path], ...]:
        roots = (self.root, self.home) if self.home else (self.root,)
        return tuple((root, root.resolve()) for root in roots)

    @cached_property
    def _destinations(self) -> tuple[tuple[Path, Path], ...]:
        return tuple(
            (mapping.destination, entry_path(mapping.destination)) for mapping in self.mappings
        )

    def normalize(self, path: Path) -> Path:
        boundaries, destinations = self._boundaries, self._destinations
        # The deepest root wins, so a link above a harness root resolves rather than
        # surviving as an ancestor the transaction would have to replace.
        matches = tuple(
            (base, canonical)
            for boundary, canonical in (*boundaries, *destinations)
            for base in (boundary, canonical)
            if path.is_relative_to(base)
        )
        if matches:
            base, canonical = max(matches, key=lambda match: len(match[0].parts))
            return canonical / path.relative_to(base)

        # Match outer aliases before traversing replaceable destination links.
        for ancestor in reversed((path, *path.parents)):
            entry = entry_path(ancestor)
            for _, canonical in destinations:
                if entry == canonical:
                    return canonical / path.relative_to(ancestor)
            resolved = ancestor.resolve()
            for _, canonical in boundaries:
                if resolved == canonical:
                    return canonical / path.relative_to(ancestor)
        return entry_path(path)


def inventory_layout(inventory: Inventory) -> DestinationLayout:
    home = dict(inventory.destination_environment).get("HOME")
    return DestinationLayout(inventory.root, inventory.mappings, Path(home) if home else None)
