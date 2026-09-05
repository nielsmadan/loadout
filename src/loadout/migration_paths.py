from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .migration_models import RootMapping


def entry_path(path: Path) -> Path:
    return path.parent.resolve() / path.name


@dataclass(frozen=True)
class DestinationLayout:
    root: Path
    mappings: tuple[RootMapping, ...]
    home: Path | None = None

    def normalize(self, path: Path) -> Path:
        roots = (self.root, self.home) if self.home else (self.root,)
        boundaries = tuple((root, root.resolve()) for root in roots)
        destinations = tuple(
            (mapping.destination, entry_path(mapping.destination))
            for mapping in self.mappings
            if mapping.kind != "file"
        )
        for boundary, canonical in (*boundaries, *destinations):
            for base in (boundary, canonical):
                if path.is_relative_to(base):
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
