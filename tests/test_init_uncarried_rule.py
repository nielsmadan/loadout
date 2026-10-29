"""A permission rule init cannot carry into the source refuses the plan (ADR 0033).

Rules has no Claude ask list beside Bash and MCP, so `Read(./secrets/**)` under `ask` used to
appear only among the preview's notes, and `--yes` converted with the rule gone.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.init_workflow import SKILL_ADVICE

pytestmark = pytest.mark.migration_integration

SETTINGS = '{"permissions": {"allow": ["Bash(ls)"], "ask": ["Read(./secrets/**)"]}}\n'


def _project(root: Path, settings: str) -> Path:
    (root / ".claude").mkdir(parents=True)
    (root / ".claude/settings.json").write_text(settings)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _init(root: Path, *flags: str) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "claude", *flags]


def test_a_claude_ask_rule_with_no_destination_is_an_issue(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project", SETTINGS)

    assert loadout.main(_init(root, "--dry-run", "--json")) == 2

    preview = json.loads(capsys.readouterr().out)
    assert [(i["code"], i["paths"]) for i in preview["issues"]] == [
        ("uncarried-rule", [str(root / ".claude/settings.json")])
    ]
    assert "'Read(./secrets/**)'" in preview["issues"][0]["message"]
    assert preview["recommendation"] == SKILL_ADVICE


def test_yes_leaves_the_rule_where_it_was(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project", SETTINGS)

    assert loadout.main(_init(root, "--yes")) == 2

    assert (root / ".claude/settings.json").read_text() == SETTINGS
    assert not (root / "loadout").exists()


def test_an_unrecognised_permission_entry_is_an_issue_too(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "opencode.json").write_text('{"permission": {"bash": {"git *": "sometimes"}}}\n')
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    argv = ["init", "project", "--root", str(root), "--harness", "opencode", "--dry-run", "--json"]
    assert loadout.main(argv) == 2

    issues = json.loads(capsys.readouterr().out)["issues"]
    assert [(i["code"], i["paths"]) for i in issues] == [
        ("uncarried-rule", [str(root / "opencode.json")])
    ]
    assert "'sometimes'" in issues[0]["message"]
