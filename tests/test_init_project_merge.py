"""Project permissions and MCP servers that differ across harnesses merge at init, as
they do at global scope: the stricter verdict wins, servers combine by name."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

HARNESSES = ("claude", "codex", "opencode")
CLAUDE_SETTINGS = {"permissions": {"allow": ["Bash(git status)"], "deny": ["Bash(rm:*)"]}}
CODEX_RULES = 'prefix_rule(pattern=["git", "push"], decision="forbidden")\n'
OPENCODE = {
    "$schema": "https://opencode.ai/config.json",
    "permission": {"bash": {"rm *": "deny", "git status": "ask"}},
    "mcp": {"docs": {"type": "local", "command": ["docs"]}},
}
CLAUDE_SERVERS = {"mcpServers": {"search": {"type": "stdio", "command": "search"}}}


def _project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path) -> int:
    flags = [arg for harness in HARNESSES for arg in ("--harness", harness)]
    return loadout.main(["init", "project", "--root", str(root), *flags, "--yes"])


def _files(opencode: dict[str, object] = OPENCODE) -> dict[str, str]:
    return {
        ".claude/settings.json": json.dumps(CLAUDE_SETTINGS),
        ".codex/rules/permissions.rules": CODEX_RULES,
        "opencode.json": json.dumps(opencode),
        ".mcp.json": json.dumps(CLAUDE_SERVERS),
    }


def test_every_harness_keeps_every_deny_after_init(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", _files())

    assert _init(root) == 0

    claude = json.loads((root / ".claude/settings.json").read_text())["permissions"]
    assert claude["deny"] == ["Bash(rm:*)", "Bash(git push:*)"]
    assert claude["ask"] == ["Bash(git status)"]
    codex = (root / ".codex/rules/permissions.rules").read_text()
    assert 'prefix_rule(pattern = ["rm"], decision = "forbidden")' in codex
    assert 'prefix_rule(pattern = ["git", "push"], decision = "forbidden")' in codex
    bash = json.loads((root / "opencode.json").read_text())["permission"]["bash"]
    assert (bash["rm *"], bash["git push *"], bash["git status"]) == ("deny", "deny", "ask")
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_servers_from_each_harness_combine_by_name(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", _files())

    assert _init(root) == 0

    assert sorted(json.loads((root / ".mcp.json").read_text())["mcpServers"]) == [
        "docs",
        "search",
    ]
    assert sorted(json.loads((root / "opencode.json").read_text())["mcp"]) == ["docs", "search"]


def test_a_server_defined_differently_in_two_files_still_refuses(tmp_path: Path, capsys) -> None:
    clash = {**OPENCODE, "mcp": {"search": {"type": "local", "command": ["other"]}}}
    root = _project(tmp_path / "p", _files(clash))

    assert _init(root) == 3

    assert "MCP server 'search' is defined differently" in capsys.readouterr().err
    assert json.loads((root / "opencode.json").read_text()) == clash
