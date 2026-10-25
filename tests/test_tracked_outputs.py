"""A path loadout generates that Git still tracks.

`.gitignore` does not untrack a file, so a hand-copied skill that a template later renders
stays in the index and git reports every render as a change (dreamhack/app1's
`rn-upgrade`). Sync never changes the index; it and `check` name each such path and the
command that untracks it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.commands import still_tracked_note

pytestmark = pytest.mark.integration

SKILL = "---\nname: review\ndescription: r.\n---\nBody.\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _project(root: Path) -> Path:
    """A project whose `review` skill was also copied by hand into Claude's folder."""
    files = {
        "loadout/config.toml": 'harnesses = ["claude"]\n',
        "loadout/skills/review/SKILL.md": SKILL,
        ".claude/skills/review/SKILL.md": SKILL,
        "README.md": "# App\n",
    }
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def test_sync_names_a_generated_path_git_still_tracks_and_leaves_the_index(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "project")
    index = _git(root, "ls-files", "--stage")

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0

    assert capsys.readouterr().err.splitlines() == [
        f"WARNING: {still_tracked_note('.claude/skills/review')}"
    ]
    assert _git(root, "ls-files", "--stage") == index
    assert "git rm -r --cached -- .claude/skills/review" in still_tracked_note(
        ".claude/skills/review"
    )


def test_check_notes_it_without_counting_it_as_drift(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project")
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 0

    result = json.loads(capsys.readouterr().out)
    assert (result["drift"], result["notes"]) == ([], [still_tracked_note(".claude/skills/review")])


def test_once_untracked_nothing_is_reported(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project")
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    _git(root, "rm", "-r", "-q", "--cached", "--", ".claude/skills/review")
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert capsys.readouterr().err == ""
    assert loadout.main(["check", "--root", str(root), "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["notes"] == []
