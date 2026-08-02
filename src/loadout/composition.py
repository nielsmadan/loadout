from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .errors import LoadoutError
from .resolve import resolve_fragment
from .targets import HEADER

if TYPE_CHECKING:
    from .manifest import InstructionTarget, Manifest


def load_fragment(path: Path) -> str:
    if not path.is_file():
        raise LoadoutError(f"fragment not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def render(target: InstructionTarget, manifest: Manifest) -> str:
    blocks = [HEADER]
    for name in target.fragments:
        item = resolve_fragment(manifest.sources, name)
        blocks.append(load_fragment(item.path))
    return "\n\n".join(blocks) + "\n"
