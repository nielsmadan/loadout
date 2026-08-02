from __future__ import annotations

from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    fragments = tmp_path / "global" / "fragments"
    fragments.mkdir(parents=True)
    for src in (GOLDEN / "global" / "fragments").glob("*.md"):
        (fragments / src.name).write_text(src.read_text())
    (tmp_path / "loadout.toml").write_text(
        (GOLDEN / "manifest.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path
