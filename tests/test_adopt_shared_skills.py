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
from loadout.migration import SHARED_SKILL_COPY

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


@pytest.mark.parametrize("reader", ["opencode", "pi"])
def test_identical_copies_in_a_reader_s_folder_and_the_shared_folder_are_adopted_once(
    tmp_path: Path, capsys, reader: str
) -> None:
    """Loadout renders one `.agents/skills` copy that Codex and the reader both read, so the
    reader's own copy is retired rather than rendered."""
    root = tmp_path / "project"
    shared = _write(root / ".agents/skills/review/SKILL.md", SKILL.format(name="review"))
    own = _write(root / f".{reader}/skills/review/SKILL.md", SKILL.format(name="review"))
    _write(root / f".{reader}/skills/review/notes.md", "Notes.\n")
    _write(root / ".agents/skills/review/notes.md", "Notes.\n")
    _git(root, "init", "-q")
    init = ["init", "project", "--root", str(root), "--harness", "codex", "--harness", reader]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0
    originals = json.loads(capsys.readouterr().out)["originals"]
    assert [(o["action"], o["reason"]) for o in originals if o["path"] == str(own)] == [
        ("retire", SHARED_SKILL_COPY)
    ]
    assert loadout.main([*init, "--yes"]) == 0

    assert (root / "loadout/skills/review/notes.md").read_text() == "Notes.\n"
    ignored = (root / ".gitignore").read_text().splitlines()
    assert [line for line in ignored if "skills" in line] == ["/.agents/skills/review/"]
    assert shared.read_text().endswith("Body.\n")
    assert (own.exists(), (own.parent / "notes.md").exists()) == (False, False)
    capsys.readouterr()
    for command in ("check", "sync", "check"):
        assert loadout.main([command, "--root", str(root)]) == 0


def test_differing_copies_in_a_reader_s_folder_and_the_shared_folder_stay_a_conflict(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    _write(root / ".agents/skills/review/SKILL.md", SKILL.format(name="review"))
    _write(root / ".opencode/skills/review/SKILL.md", SKILL.format(name="review") + "More.\n")
    _git(root, "init", "-q")
    init = ["init", "project", "--root", str(root), "--harness", "codex", "--harness", "opencode"]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0

    unadopted = json.loads(capsys.readouterr().out)["unadopted"]
    assert [(u["kind"], u["name"]) for u in unadopted] == [("skill", "review")]


def test_without_codex_a_reader_s_own_copy_becomes_generated(tmp_path: Path, capsys) -> None:
    """Only Codex's render goes to `.agents/skills`, so OpenCode keeps its own folder."""
    root = tmp_path / "project"
    own = _write(root / ".opencode/skills/review/SKILL.md", SKILL.format(name="review"))
    _git(root, "init", "-q")
    init = ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "opencode"]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0

    originals = json.loads(capsys.readouterr().out)["originals"]
    assert [o["action"] for o in originals if o["path"] == str(own)] == ["generated"]


def test_init_ignores_an_adopted_skill_once_as_a_folder(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    skill = _write(root / ".claude/skills/lint/SKILL.md", SKILL.format(name="lint"))
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")

    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"]) == 0
    )
    assert loadout.main(["sync", "--root", str(root)]) == 0

    ignored = (root / ".gitignore").read_text().splitlines()
    assert [line for line in ignored if "skills" in line] == ["/.claude/skills/lint/"]
    assert (
        subprocess.run(
            ["git", "-C", str(root), "ls-files", "--", str(skill)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
