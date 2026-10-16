"""The staged check writes out only what a render can read, not the whole repository
(review 2.8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import staged
from loadout.staged import _entries, _render_inputs
from registration import register

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


def test_the_head_snapshot_holds_only_what_ownership_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance, should-fix 1: a global source at a repository's root wrote the whole
    of HEAD to temp, though the HEAD pass reads only the manifests and project config."""
    register(tmp_path)
    root = _repository(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "own"\npath = "."\nuse = ["skills"]\n\n[claude]\n',
            "claude.toml": "",
            "work.toml": 'extends = "default"\n',
            "skills/a/SKILL.md": "---\nname: a\ndescription: A.\n---\nBody.\n",
            "assets/big.bin": "payload\n",
        },
    )
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        check=True,
    )
    written: dict[str, list[str]] = {}
    snapshot = staged._snapshot

    def recording(repository: Path, destination: Path, entries: dict[str, staged.Entry]) -> None:
        written[destination.name] = sorted(entries)
        snapshot(repository, destination, entries)

    monkeypatch.setattr(staged, "_snapshot", recording)

    assert staged.check_staged(root, "work") == 0

    assert written["head"] == ["claude.toml", "loadout.toml", "work.toml"]
    assert "assets/big.bin" in written["index"]
