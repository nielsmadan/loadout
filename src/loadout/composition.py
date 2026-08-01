from __future__ import annotations

from pathlib import Path

from .errors import LoadoutError


def load_fragment(fragments_dir: Path, name: str) -> str:
    path = fragments_dir / f"{name}.md"
    if not path.is_file():
        raise LoadoutError(f"fragment not found: {path}")
    return path.read_text().strip()
