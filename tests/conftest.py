from __future__ import annotations

from pathlib import Path

import pytest

from fixture_root import build_project_root, build_root


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
    return build_root(tmp_path)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return build_project_root(tmp_path)
