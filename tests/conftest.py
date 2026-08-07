from __future__ import annotations

import json
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets an isolated HOME, never the real one.

    Destinations resolve `~` via Path.expanduser(), which reads HOME. Without
    this, any test that touches render_global/render_all/write_all/the CLI
    would expand `~` to the real developer machine's home directory.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    instructions = tmp_path / "instructions"
    instructions.mkdir(parents=True)
    for src in (GOLDEN / "instructions").glob("*.md"):
        (instructions / src.name).write_text(src.read_text())

    (tmp_path / "permissions.toml").write_text(
        (GOLDEN / "permissions.toml").read_text(encoding="utf-8"),
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
