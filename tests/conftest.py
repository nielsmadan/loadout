from __future__ import annotations

from pathlib import Path

import pytest

from fixture_root import build_project_root, build_root

HARNESS_VARIABLES = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "XDG_CONFIG_HOME",
    "LOADOUT_TEST_HARNESS_C",
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets an isolated HOME, never the real one.

    Destinations resolve `~` via Path.expanduser(), which reads HOME. Without
    this, any test that touches render_global/render_all/write_all/the CLI
    would expand `~` to the real developer machine's home directory.

    The harness variables go the same way: a destination template reads them, so a
    developer who has one exported would otherwise render to a different path than
    CI does, and the expected tree would stop being machine-independent.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    for variable in HARNESS_VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    return home


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return build_root(tmp_path)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project_root(tmp_path)
