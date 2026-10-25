"""What init's closing line says it left in the index.

Init commits nothing, but it removes each adopted original that loadout now generates from
the index, so those removals are staged. dreamhack/app1 was told "nothing was committed or
staged" with three staged deletions.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _init(root: Path) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "codex"]


def test_init_names_the_adopted_files_it_removed_from_the_index(tmp_path: Path, capsys) -> None:
    root = _project(
        tmp_path / "project",
        {
            "AGENTS.md": "# App\n\nRules.\n",
            ".claude/settings.json": '{"permissions": {"allow": ["Bash(yarn test*)"]}}\n',
            "README.md": "# App\n",
        },
    )

    assert loadout.main([*_init(root), "--yes"]) == 0

    staged = _git(root, "diff", "--cached", "--name-status").splitlines()
    assert staged == ["D\t.claude/settings.json", "D\tAGENTS.md"]
    lines = capsys.readouterr().out.splitlines()
    closing = lines.index(
        "Migration complete; nothing was committed. 2 adopted file(s) loadout now generates "
        "were removed from the index, so `git status` shows them as staged deletions:"
    )
    assert lines[closing + 1 : closing + 3] == ["  .claude/settings.json", "  AGENTS.md"]


def test_init_that_removed_nothing_from_the_index_says_nothing_was_staged(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "project", {"README.md": "# App\n"})
    (root / "AGENTS.md").write_text("# App\n\nRules.\n")

    assert loadout.main([*_init(root), "--yes"]) == 0

    assert _git(root, "diff", "--cached", "--name-only") == ""
    assert "Migration complete; nothing was committed or staged." in capsys.readouterr().out


def test_the_json_result_lists_the_removals(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project", {"AGENTS.md": "# App\n\nRules.\n"})

    assert loadout.main([*_init(root), "--yes", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["untracked"] == ["AGENTS.md"]
