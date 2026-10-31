"""Project init names Droid's legacy `.factory/hooks/hooks.json` instead of passing over it.

Factory's hooks reference lists `.factory/hooks/hooks.json` as legacy: "Still loads. The next save
writes `.factory/hooks.json` and archives the old file as `hooks/hooks.migrated.json`." Project
init did not read that folder, so the file kept running unmentioned.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.discovery import DROID_LEGACY_HOOKS

pytestmark = pytest.mark.migration_integration

EVENTS = {"Stop": [{"hooks": [{"type": "command", "command": "/bin/echo done"}]}]}


def test_project_init_keeps_droid_s_legacy_hooks_file_in_place_and_says_why(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "p"
    legacy = root / ".factory/hooks/hooks.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(EVENTS))
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "droid", "--dry-run"])
        == 0
    )

    assert f"Keep in place: {legacy} ({DROID_LEGACY_HOOKS})" in capsys.readouterr().out
    assert "hooks.migrated.json" in DROID_LEGACY_HOOKS
