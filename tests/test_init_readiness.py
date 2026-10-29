"""The preview's category rows say what the source can render.

An empty claude+codex project listed `agents` as "unsupported" though source agents render to
every harness at both scopes, and Claude's project permissions the same way though
`.claude/settings.json` carries them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration


def _states(out: str) -> dict[tuple[str, str], str]:
    return {
        (row["category"], harness): row["state"]
        for row in json.loads(out)["categories"]
        for harness in row["harnesses"]
    }


def test_an_empty_project_routes_agents_and_offers_claude_permissions(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "codex"]

    assert loadout.main([*argv, "--dry-run", "--json"]) == 0

    states = _states(capsys.readouterr().out)
    assert states[("agents", "claude")] == states[("agents", "codex")] == "routed"
    assert states[("permissions", "claude")] == "not-adopted"
    assert states[("plugins", "claude")] == "unsupported"


def test_a_global_init_routes_agents(tmp_path: Path, fake_home: Path, capsys) -> None:
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n')
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "claude", "--dry-run", "--json"]
    assert loadout.main(argv) == 0

    assert _states(capsys.readouterr().out)[("agents", "claude")] == "routed"
