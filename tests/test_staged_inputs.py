"""The staged check writes out only what a render can read, not the whole repository
(review 2.8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout.staged import _entries, _render_inputs

pytestmark = pytest.mark.integration


def _repository(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def test_a_project_reads_its_loadout_folder_and_root_files(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude"]\n',
            "loadout/skills/s/SKILL.md": "x\n",
            "CLAUDE.md": "@AGENTS.md\n",
            "src/app/big.bin": "payload\n",
            "docs/guide.md": "guide\n",
        },
    )

    inputs = _render_inputs(root, Path("."), _entries(root))

    assert sorted(inputs) == ["CLAUDE.md", "loadout/config.toml", "loadout/skills/s/SKILL.md"]


def test_a_global_source_reads_its_folder_and_named_sources_outside_it(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "config/loadout.toml": '[[source]]\nname = "s"\npath = "../shared"\n',
            "config/skills/a/SKILL.md": "a\n",
            "shared/permissions.toml": "[shell]\n",
            "unrelated/notes.md": "notes\n",
        },
    )

    inputs = _render_inputs(root, Path("config"), _entries(root))

    assert sorted(inputs) == [
        "config/loadout.toml",
        "config/skills/a/SKILL.md",
        "shared/permissions.toml",
    ]
