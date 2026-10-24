"""A project harness skills folder that links to a real folder in the same repository.

Sharing one skills folder by linking `.claude/skills` to `.agents/skills` is common. init
reads through such a link and, at apply, replaces it with a real folder loadout renders,
since sync never writes through a link. A link to a folder the same harness already reads,
such as `.opencode/skills` to `.agents/skills`, is retired instead. Every other link stays
refused: out of the repository, into `.git`, or anything but a harness skills folder.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

SKILL = "---\nname: {name}\ndescription: {name}.\n---\nBody.\n"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _shared(root: Path, *links: str) -> Path:
    """`.agents/skills` with two skills and a folder of shared scripts that is no skill,
    and each named harness folder linking to it."""
    for name in ("review", "deploy"):
        path = root / f".agents/skills/{name}/SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(SKILL.format(name=name))
    (root / ".agents/skills/shared").mkdir()
    (root / ".agents/skills/shared/lint.sh").write_text("true\n")
    for link in links:
        (root / link).parent.mkdir(parents=True, exist_ok=True)
        (root / link).symlink_to("../.agents/skills")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _init(root: Path, *harnesses: str, mode: str = "--yes") -> list[str]:
    flags = [flag for harness in harnesses for flag in ("--harness", harness)]
    return ["init", "project", "--root", str(root), *flags, mode]


def _tracked(root: Path, path: str) -> str:
    return _git(root, "ls-files", "--stage", "--", path)


def _converted(root: Path, capsys) -> tuple[int, int, int]:
    capsys.readouterr()
    return tuple(
        loadout.main([command, "--root", str(root)]) for command in ("check", "sync", "check")
    )  # type: ignore[return-value]


@pytest.mark.parametrize("harnesses", [("claude", "codex"), ("claude", "pi")])
def test_a_claude_skills_folder_linked_into_the_repository_becomes_a_real_folder(
    tmp_path: Path, capsys, harnesses: tuple[str, ...]
) -> None:
    """sentry (with Codex) and Integralist/agent-skills (with Pi)."""
    root = _shared(tmp_path / "project", ".claude/skills")

    assert loadout.main(_init(root, *harnesses)) == 0

    skills = root / ".claude/skills"
    assert (skills.is_symlink(), skills.is_dir()) == (False, True)
    assert sorted(p.name for p in skills.iterdir()) == ["deploy", "review"]
    assert (skills / "review/SKILL.md").read_text().endswith("Body.\n")
    assert (root / ".agents/skills/shared/lint.sh").read_text() == "true\n"
    assert "?? " not in _git(root, "status", "--porcelain", "-uall", "--", ".claude")
    assert (root / ".agents/skills/review/SKILL.md").read_text() == SKILL.format(name="review")
    assert sorted(p.parent.name for p in (root / "loadout/skills").glob("*/SKILL.md")) == [
        "deploy",
        "review",
    ]
    assert _tracked(root, ".claude/skills") == ""
    # Codex renders into `.agents/skills`, so only then do its files leave the index.
    assert bool(_tracked(root, ".agents/skills/review/SKILL.md")) == ("codex" not in harnesses)
    assert _converted(root, capsys) == (0, 0, 0)


def test_a_link_to_a_folder_the_harness_already_reads_is_retired(tmp_path: Path, capsys) -> None:
    """CodelyTV/agent-harness: OpenCode reads `.agents/skills` itself."""
    root = _shared(tmp_path / "project", ".claude/skills", ".opencode/skills")

    assert loadout.main(_init(root, "claude", "codex", "opencode")) == 0

    retired = root / ".opencode/skills"
    assert (retired.is_symlink(), retired.exists()) == (False, False)
    assert _git(root, "status", "--porcelain", "--", ".opencode/skills") == " D .opencode/skills\n"
    assert (root / ".claude/skills").is_symlink() is False
    assert (root / ".agents/skills/deploy/SKILL.md").read_text() == SKILL.format(name="deploy")
    assert _converted(root, capsys) == (0, 0, 0)


@pytest.mark.parametrize(
    ("link", "target", "code"),
    [
        (".claude/skills", "{outside}", "external-symlink"),
        (".claude/skills", "../.git/info", "git-link"),
        (".claude/agents", "../.agents/skills", "linked-folder"),
        (".claude/skills/review", "../../.agents/skills/review", "linked-folder"),
    ],
    ids=["out-of-the-repository", "into-git", "not-a-skills-folder", "inside-a-skills-folder"],
)
def test_every_other_link_is_still_refused(
    tmp_path: Path, capsys, link: str, target: str, code: str
) -> None:
    root = tmp_path / "project"
    outside = tmp_path / "outside"
    (outside / "lint").mkdir(parents=True)
    (outside / "lint/SKILL.md").write_text(SKILL.format(name="lint"))
    (root / ".agents/skills/review").mkdir(parents=True)
    (root / ".agents/skills/review/SKILL.md").write_text(SKILL.format(name="review"))
    (root / link).parent.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / link).symlink_to(target.format(outside=outside))
    before = os.readlink(root / link)

    assert loadout.main([*_init(root, "claude", mode="--dry-run"), "--json"]) == 2

    issues = json.loads(capsys.readouterr().out)["issues"]
    assert [(issue["code"], issue["paths"][0]) for issue in issues] == [(code, str(root / link))]
    assert os.readlink(root / link) == before


def test_a_linked_folder_holding_what_its_harness_loads_but_loadout_does_not_render_is_refused(
    tmp_path: Path, capsys
) -> None:
    """OpenCode loads a root .md file as a skill; init leaves it in place, so replacing the
    link with a folder of loadout's skills would drop it."""
    root = tmp_path / "project"
    (root / "skills/review").mkdir(parents=True)
    (root / "skills/review/SKILL.md").write_text(SKILL.format(name="review"))
    (root / "skills/notes.md").write_text("Notes.\n")
    (root / ".opencode").mkdir()
    (root / ".opencode/skills").symlink_to("../skills")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")

    assert loadout.main(_init(root, "opencode", mode="--yes")) == 3

    refusal = capsys.readouterr().err.splitlines()[-1]
    notes = root / ".opencode/skills/notes.md"
    assert refusal.startswith(f"loadout: {notes} is not one of the skills loadout renders")
    assert os.readlink(root / ".opencode/skills") == "../skills"
    assert _git(root, "status", "--porcelain") == ""
