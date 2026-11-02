"""Pi's project servers live in `.pi/mcp.json`, the file its built-in MCP reads once the
project is trusted. `.mcp.json` was pi-mcp-adapter's project file and is Claude's alone now."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.discovery import PI_ADAPTER_FILE

pytestmark = pytest.mark.migration_integration

SERVERS = {
    "mcpServers": {
        "docs": {"url": "https://docs.test/mcp", "headers": {"Authorization": "Bearer ${DOCS}"}},
        "local": {"command": "srv", "args": ["-v"]},
    }
}


def _project(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def test_project_init_adopts_pi_mcp_json_and_renders_it_back(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / ".pi").mkdir()
    rendered = json.dumps(SERVERS, indent=2) + "\n"
    (root / ".pi/mcp.json").write_text(rendered)

    assert loadout.main(["init", "project", "--root", str(root), "--harness", "pi", "--yes"]) == 0

    source = tomllib.loads((root / "loadout/mcp.toml").read_text())
    assert source["docs"] == {
        "transport": "http",
        "url": "https://docs.test/mcp",
        "auth_env_var": "DOCS",
    }
    assert source["local"] == {"transport": "stdio", "command": "srv", "args": ["-v"]}
    assert json.loads((root / ".pi/mcp.json").read_text()) == SERVERS
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_a_project_server_renders_to_pi_mcp_json(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / "loadout").mkdir()
    (root / "loadout/config.toml").write_text('harnesses = ["pi"]\n')
    (root / "loadout/mcp.toml").write_text('[local]\ntransport = "stdio"\ncommand = "srv"\n')

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads((root / ".pi/mcp.json").read_text()) == {
        "mcpServers": {"local": {"command": "srv", "args": []}}
    }


def test_project_init_keeps_an_adapter_mcp_json_in_place_and_says_why(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    (root / ".mcp.json").write_text(json.dumps(SERVERS))

    argv = ["init", "project", "--root", str(root), "--harness", "pi", "--dry-run"]
    assert loadout.main(argv) == 0

    assert f"Keep in place: {root / '.mcp.json'} ({PI_ADAPTER_FILE})" in capsys.readouterr().out


@pytest.mark.parametrize(
    "relative", [".agents/mcp.json", ".agents/mcp/mcp.json", ".config/mcp/mcp.json"]
)
def test_global_init_keeps_an_adapter_server_file_in_place_and_says_why(
    tmp_path: Path, fake_home: Path, capsys, relative: str
) -> None:
    adapter = fake_home / relative
    adapter.parent.mkdir(parents=True, exist_ok=True)
    adapter.write_text(json.dumps(SERVERS))
    source = _project(tmp_path / "source")

    argv = ["init", "global", str(source), "--harness", "pi", "--dry-run"]
    assert loadout.main(argv) == 0

    assert f"Keep in place: {adapter} ({PI_ADAPTER_FILE})" in capsys.readouterr().out
