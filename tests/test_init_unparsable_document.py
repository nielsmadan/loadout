"""A native document init cannot parse is named in the refusal.

An unparsable `.claude/settings.json` or `.mcp.json` used to refuse as `source-composition`,
naming the source folder init had not yet written, so nothing pointed at the file to repair.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration


@pytest.mark.parametrize("name", [".claude/settings.json", ".mcp.json"])
def test_an_unparsable_document_is_refused_by_its_own_path(
    tmp_path: Path, capsys, name: str
) -> None:
    root = tmp_path / "project"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude/settings.json").write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n')
    (root / name).write_text('{"permissions": {\n')
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--dry-run", "--json"]
    assert loadout.main(argv) == 2

    issues = json.loads(capsys.readouterr().out)["issues"]
    assert [(issue["code"], issue["paths"]) for issue in issues] == [
        ("invalid-native-document", [str(root / name)])
    ]
