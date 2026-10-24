"""init adopts the shared `.agents/skills` only for a harness loadout renders it back for.

Codex, OpenCode and Pi all read `.agents/skills`, but loadout writes their global skills to
each harness's own folder and their project skills to `.agents/skills` only for Codex. A
skill adopted for another harness renders somewhere else, so it stays where it is and the
harness keeps reading it there.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

SKILL = "---\nname: {name}\ndescription: {name}.\n---\nBody.\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _migrated(preview: dict) -> dict[str, list[str]]:
    return {
        c["path"]: c["harnesses"]
        for c in preview["candidates"]
        if c["disposition"] == "migrated" and c["category"] == "skills"
    }


def test_a_global_shared_skills_folder_stays_in_place_for_the_harnesses_that_read_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    shared = _write(fake_home / ".agents/skills/review/SKILL.md", SKILL.format(name="review"))
    own = _write(fake_home / ".claude/skills/deploy/SKILL.md", SKILL.format(name="deploy"))
    source = tmp_path / "agent-config"
    source.mkdir()
    harnesses = ("claude", "codex", "opencode", "pi")
    init = ["init", "global", str(source), *(f for h in harnesses for f in ("--harness", h))]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0
    assert _migrated(json.loads(capsys.readouterr().out)) == {str(own): ["claude"]}
    assert loadout.main([*init, "--yes"]) == 0

    assert shared.read_text() == SKILL.format(name="review")
    assert sorted(p.relative_to(source).as_posix() for p in source.rglob("SKILL.md")) == [
        "loadout/skills/deploy/SKILL.md"
    ]
    capsys.readouterr()
    assert loadout.main(["check", "--global"]) == 0


def test_a_project_shared_skills_folder_is_adopted_for_codex_which_loadout_writes_there(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    shared = _write(root / ".agents/skills/review/SKILL.md", SKILL.format(name="review"))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    init = ["init", "project", "--root", str(root), "--harness", "codex", "--harness", "opencode"]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0
    assert _migrated(json.loads(capsys.readouterr().out)) == {str(shared): ["codex"]}
    assert loadout.main([*init, "--yes"]) == 0

    assert (root / "loadout/skills/review/SKILL.md").read_text() == SKILL.format(name="review")
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0
