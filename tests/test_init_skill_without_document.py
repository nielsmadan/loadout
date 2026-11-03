"""A skill folder holding SKILL.md only below its top is refused by its own path.

The refusal named the planned source folder, so it did not say which skill to fix.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration


def test_init_names_the_skill_folder_without_skill_md(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    folder = fake_home / ".codex/skills/team"
    (folder / "review").mkdir(parents=True)
    (folder / "review/SKILL.md").write_text("---\nname: review\ndescription: d\n---\nx\n")
    source = tmp_path / "agent-config"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)

    argv = ["init", "global", str(source), "--harness", "codex", "--json"]
    assert loadout.main(argv) == 2
    assert json.loads(capsys.readouterr().out)["issues"] == [
        {
            "code": "source-composition",
            "paths": [str(folder)],
            "message": (
                "This skill folder holds SKILL.md only below its top, so it is not one skill "
                "init can adopt. Move each skill in it to the top of the skills folder, then "
                "run init again."
            ),
        }
    ]
