"""What init does with a Pi permission document holding more than loadout carries.

loadout 1.0 renders only Pi's shell and MCP rules into pi-permission-system's config. Any other
key the extension reads is refused when dropping it changes what Pi allows, and noted when it
does not (the user's ruling on review question 1).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

GLOBAL = ".pi/agent/extensions/pi-permission-system/config.json"
PROJECT = ".pi/extensions/pi-permission-system/config.json"


def _git(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _write(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def _global(tmp_path: Path, home: Path, document: object, capsys) -> tuple[int, dict, Path]:
    original = _write(home / GLOBAL, document)
    argv = ["init", "global", str(_git(tmp_path / "src")), "--harness", "pi", "--dry-run"]
    code = loadout.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out), original


def _project(tmp_path: Path, document: object, capsys) -> tuple[int, dict, Path]:
    root = _git(tmp_path / "p")
    original = _write(root / PROJECT, document)
    argv = ["init", "project", "--root", str(root), "--harness", "pi", "--dry-run", "--json"]
    code = loadout.main(argv)
    return code, json.loads(capsys.readouterr().out), original


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_deny_with_a_reason_is_refused_as_a_rule_init_cannot_read(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    document = {"permission": {"bash": {"npm *": {"action": "deny", "reason": "Use pnpm"}}}}
    code, preview, original = (
        _global(tmp_path, fake_home, document, capsys)
        if scope == "global"
        else _project(tmp_path, document, capsys)
    )

    label = "pi" if scope == "global" else "pi-project"
    assert code == 2
    assert preview["issues"] == [
        {
            "code": "uncarried-rule",
            "paths": [str(original)],
            "message": (
                "The source has no place for these rules, so converting would lose them: "
                f"{label}: decision {{'action': 'deny', 'reason': 'Use pnpm'}} for 'npm *'."
            ),
        }
    ]
