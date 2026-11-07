from pathlib import Path

BUNDLED = "(bundled)"
BUNDLED_KITS = ("frontend", "backend")


def bundled_kit(name: str) -> Path | None:
    if name not in BUNDLED_KITS:
        return None
    return Path(__file__).parent / "_kits" / name
