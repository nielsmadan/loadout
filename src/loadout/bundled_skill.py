from __future__ import annotations

from pathlib import Path

from .errors import LoadoutError

SKILL_NAME = "loadout"


def bundled_skill_path(package_dir: Path | None = None) -> Path:
    package = Path(__file__).parent if package_dir is None else package_dir
    packaged = package / "_skills" / SKILL_NAME
    if (packaged / "SKILL.md").is_file():
        return packaged.resolve()
    raise LoadoutError(f"bundled skill not found at {packaged}")
