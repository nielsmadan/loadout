from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPE = re.compile(r"^([a-z][a-z0-9-]*)(?:\s+[^:]*)?:", re.MULTILINE)


def _justfile_recipes() -> set[str]:
    return set(RECIPE.findall((ROOT / "Justfile").read_text(encoding="utf-8")))


def test_release_checks_name_real_justfile_recipes() -> None:
    config = json.loads((ROOT / "scripts/release.json").read_text(encoding="utf-8"))
    named = [command[1] for command in config["checks"] if command[0] == "just"]
    assert named, "expected release checks to run just recipes"
    missing = sorted(set(named) - _justfile_recipes())
    assert not missing, f"release.json names recipes the Justfile does not define: {missing}"
