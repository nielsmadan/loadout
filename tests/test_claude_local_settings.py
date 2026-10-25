"""A project's `.claude/settings.local.json` stays in place, and init says so.

Loadout renders no settings.local.json (ADR 0024), but Claude still applies its permissions
and enabled servers, so the preview names it with a pointer to the personal tier.
dreamhack/app1 had stale personal permissions there that init passed over in silence.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.discovery import CLAUDE_LOCAL_SETTINGS

pytestmark = pytest.mark.migration_integration

LOCAL = '{"permissions": {"allow": ["Bash(adb devices)"]}, "enabledMcpjsonServers": ["ios-sim"]}\n'


def _project(root: Path) -> Path:
    (root / ".claude").mkdir(parents=True)
    (root / ".claude/settings.json").write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n')
    (root / ".claude/settings.local.json").write_text(LOCAL)
    (root / ".gitignore").write_text(".claude/settings.local.json\n")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path, *flags: str) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "claude", *flags]


def test_the_preview_keeps_claude_s_local_settings_in_place_and_says_why(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "project")
    local = root / ".claude/settings.local.json"

    assert loadout.main(_init(root, "--dry-run")) == 0

    assert f"Keep in place: {local} ({CLAUDE_LOCAL_SETTINGS})" in capsys.readouterr().out
    assert "permissions.local.toml" in CLAUDE_LOCAL_SETTINGS
    assert "/loadout" in CLAUDE_LOCAL_SETTINGS


def test_init_leaves_claude_s_local_settings_as_they_were(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project")
    local = root / ".claude/settings.local.json"

    assert loadout.main(_init(root, "--yes", "--json")) == 0

    assert local.read_text() == LOCAL
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_the_json_preview_lists_it_as_retained(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project")

    assert loadout.main(_init(root, "--dry-run", "--json")) == 0

    originals = json.loads(capsys.readouterr().out)["originals"]
    local = str(root / ".claude/settings.local.json")
    assert [(o["action"], o["reason"]) for o in originals if o["path"] == local] == [
        ("retain", CLAUDE_LOCAL_SETTINGS)
    ]
