"""Init adopts a Devin setup: config.json splits into permissions, hooks and the settings the
user wrote, leaving the keys the CLI writes for itself; the server file and the global
instructions are adopted beside it, and a sync writes them all back unchanged."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

CONFIG = {
    "version": 1,
    "permission_mode": "accept-edits",
    "agent": {"model": "swe"},
    "permissions": {
        "allow": ["Exec(git status)", "ExecExact(npm test)", "mcp__docs__*"],
        "ask": ["Exec(git push)"],
        "deny": ["Exec(rm)"],
    },
    "hooks": {
        "PreToolUse": [{"matcher": "^exec$", "hooks": [{"type": "command", "command": "guard.sh"}]}]
    },
    "devin": {"org_id": "org-1"},
    "shell": {"setup_complete": True},
    "theme_mode": "dark",
}
SERVERS = {"mcpServers": {"docs": {"command": "docs-mcp", "args": [], "transport": "stdio"}}}


@pytest.fixture
def adopted(tmp_path: Path, fake_home: Path) -> tuple[Path, Path]:
    root = fake_home / ".config" / "devin"
    root.mkdir(parents=True)
    (root / "config.json").write_text(json.dumps(CONFIG, indent=2) + "\n")
    (root / "mcp_config.json").write_text(json.dumps(SERVERS, indent=2) + "\n")
    (root / "AGENTS.md").write_text("# Mine\n\nBe brief.\n")
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    assert loadout.main(["init", "global", str(source), "--harness", "devin", "--yes"]) == 0
    return source / "loadout", root


def test_config_json_splits_into_its_slices_and_the_clis_keys_stay_behind(
    adopted: tuple[Path, Path],
) -> None:
    source, _ = adopted
    block = tomllib.loads((source / "loadout.toml").read_text())["devin"]
    settings = json.loads((source / "settings" / f"{block['settings'][0]}.json").read_text())
    assert settings == {"permission_mode": "accept-edits", "agent": {"model": "swe"}}
    hooks = json.loads((source / "hooks" / f"{block['hooks'][0]}.json").read_text())
    assert hooks == {
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}]
    }
    permissions = tomllib.loads((source / "permissions.toml").read_text())
    assert permissions["shell"] == {
        "allow": ["git status", "=npm test"],
        "ask": ["git push"],
        "deny": ["rm"],
    }
    assert permissions["mcp"] == {"allow": ["docs/*"], "ask": [], "deny": []}


def test_a_sync_after_adoption_leaves_every_devin_file_as_it_was(
    adopted: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, root = adopted
    before = {name: (root / name).read_text() for name in ("config.json", "mcp_config.json")}
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert loadout.main(["check", "--global"]) == 0
    assert json.loads((root / "config.json").read_text()) == json.loads(before["config.json"])
    assert json.loads((root / "mcp_config.json").read_text()) == SERVERS
    assert "Be brief." in (root / "AGENTS.md").read_text()


def test_project_files_are_adopted_and_written_back(tmp_path: Path, fake_home: Path) -> None:
    project = tmp_path / "app"
    devin = project / ".devin"
    devin.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    config = {"read_config_from": {"claude": False}, "permissions": {"deny": ["Exec(rm)"]}}
    (devin / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    hooks = {"Stop": [{"hooks": [{"type": "command", "command": "./done.sh"}]}]}
    (devin / "hooks.v1.json").write_text(json.dumps(hooks, indent=2) + "\n")
    (devin / "mcp_config.json").write_text(json.dumps(SERVERS, indent=2) + "\n")

    assert (
        loadout.main(["init", "project", "--root", str(project), "--harness", "devin", "--yes"])
        == 0
    )
    assert loadout.main(["sync", "--root", str(project)]) == 0

    written = json.loads((devin / "config.json").read_text())
    assert written["read_config_from"] == {"claude": False}
    assert written["permissions"]["deny"] == ["Exec(rm)"]
    assert json.loads((devin / "hooks.v1.json").read_text()) == hooks
    assert json.loads((devin / "mcp_config.json").read_text()) == SERVERS
