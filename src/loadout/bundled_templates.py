from pathlib import Path

BUNDLED = "(bundled)"
STARTERS = ("frontend", "backend")


def bundled_template(name: str) -> Path | None:
    if name not in STARTERS:
        return None
    return Path(__file__).parent / "_templates" / name
