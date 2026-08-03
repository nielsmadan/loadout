from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    fragments = tmp_path / "global" / "fragments"
    fragments.mkdir(parents=True)
    for src in (GOLDEN / "global" / "fragments").glob("*.md"):
        (fragments / src.name).write_text(src.read_text())

    permissions = tmp_path / "permissions"
    permissions.mkdir(parents=True)
    (permissions / "permissions.toml").write_text(
        (GOLDEN / "permissions" / "permissions.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for base in (
        "claude/settings.base.json",
        "claude/settings.autonomous.base.json",
        "opencode/opencode.base.json",
    ):
        dest = tmp_path / base
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text((GOLDEN / base).read_text(encoding="utf-8"), encoding="utf-8")

    # Model the co-owner (~/ac/mcp/sync.py) having already written its key, so
    # `preserve = ["mcp"]` has something to read. Without this the OpenCode
    # golden cannot match: `mcp` is passed through, not generated.
    expected_opencode = json.loads(
        (GOLDEN / "expected" / "opencode" / "opencode.json").read_text(encoding="utf-8")
    )
    (tmp_path / "opencode").mkdir(parents=True, exist_ok=True)
    (tmp_path / "opencode" / "opencode.json").write_text(
        json.dumps({"mcp": expected_opencode["mcp"]}, indent=2) + "\n", encoding="utf-8"
    )

    (tmp_path / "loadout.toml").write_text(
        (GOLDEN / "manifest.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path
