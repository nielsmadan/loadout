from __future__ import annotations

from pathlib import Path

from .errors import LoadoutError
from .targets import HEADER, Target


def load_fragment(fragments_dir: Path, name: str) -> str:
    path = fragments_dir / f"{name}.md"
    if not path.is_file():
        raise LoadoutError(f"fragment not found: {path}")
    return path.read_text().strip()


def render(target: Target, fragments_dir: Path) -> str:
    blocks = [HEADER, target.intro]
    blocks.extend(load_fragment(fragments_dir, name) for name in target.fragments)
    return "\n\n".join(blocks) + "\n"
