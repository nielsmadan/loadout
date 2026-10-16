"""A skill that names an agent hands its work to it: a fork on Claude, a delegation line
in every other harness's copy, and an error when no source agent has that name."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global, render_project
from loadout.errors import LoadoutError
from loadout.output import Copied
from loadout.skills import BANNER, discover_skills, render_skill

SKILL = (
    "---\nname: audit\ndescription: Audit a diff.\nagent: reviewer\n---\n\n# Audit\n\nCheck it.\n"
)
AGENT = "---\ndescription: Reviews a diff.\n---\n\nReview it.\n"
DELEGATION = "Delegate this task to the `reviewer` subagent."


def _skill(tmp_path: Path, text: str = SKILL):
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "SKILL.md").write_text(text)
    (skill,) = discover_skills(tmp_path)
    return skill


def test_claude_forks_into_the_agent(tmp_path: Path) -> None:
    rendered = render_skill(_skill(tmp_path), "claude")

    assert rendered.startswith(
        "---\nname: audit\ndescription: Audit a diff.\ncontext: fork\nagent: reviewer\n---\n"
    )


def test_a_stated_fork_is_not_written_twice(tmp_path: Path) -> None:
    text = SKILL.replace("agent: reviewer", "context: fork\nagent: reviewer")

    rendered = render_skill(_skill(tmp_path, text), "claude")

    assert rendered.startswith(
        "---\nname: audit\ndescription: Audit a diff.\ncontext: fork\nagent: reviewer\n---\n"
    )


@pytest.mark.parametrize("harness", ["codex", "droid", "opencode", "pi"])
def test_other_harnesses_get_a_delegation_line_before_the_task(
    tmp_path: Path, harness: str
) -> None:
    rendered = render_skill(_skill(tmp_path), harness)

    banner = BANNER.format(name="audit")
    assert rendered == (
        f"---\nname: audit\ndescription: Audit a diff.\n---\n\n{banner}\n\n"
        f"{DELEGATION}\n\n# Audit\n\nCheck it.\n"
    )


def _global_source(root: Path, *, agent: bool) -> Path:
    (root / "skills" / "audit").mkdir(parents=True)
    (root / "skills" / "audit" / "SKILL.md").write_text(SKILL)
    if agent:
        (root / "agents").mkdir()
        (root / "agents" / "reviewer.md").write_text(AGENT)
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\nskills = true\n\n[codex]\nskills = true\n'
    )
    return root


def test_a_global_skill_naming_a_defined_agent_renders(tmp_path: Path, fake_home: Path) -> None:
    outputs = render_global(_global_source(tmp_path / "source", agent=True))

    assert DELEGATION in str(outputs[fake_home / ".codex/skills/audit/SKILL.md"])
    assert "context: fork\nagent: reviewer" in str(
        outputs[fake_home / ".claude/skills/audit/SKILL.md"]
    )


def test_a_global_skill_naming_an_unknown_agent_is_an_error(
    tmp_path: Path, fake_home: Path
) -> None:
    source = _global_source(tmp_path / "source", agent=False)

    with pytest.raises(LoadoutError, match="skill 'audit' hands its work to agent 'reviewer'"):
        render_global(source)


def test_a_project_skill_naming_an_unknown_agent_is_an_error(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "loadout" / "skills" / "audit").mkdir(parents=True)
    (root / "loadout" / "skills" / "audit" / "SKILL.md").write_text(SKILL)
    (root / "loadout" / "config.toml").write_text('harnesses = ["claude", "codex"]\n')

    with pytest.raises(LoadoutError, match="skill 'audit' hands its work to agent 'reviewer'"):
        render_project(root)


def test_a_copied_skill_keeps_its_agent_as_written(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "loadout" / "skills" / "audit").mkdir(parents=True)
    (root / "loadout" / "skills" / "audit" / "SKILL.md").write_text(SKILL)
    (root / "loadout" / "config.toml").write_text(
        'harnesses = ["claude"]\n\n[skills.audit]\nagents = ["claude"]\nrender = false\n'
    )

    copied = render_project(root)[root / ".claude/skills/audit/SKILL.md"]

    assert isinstance(copied, Copied)
    assert copied.read_bytes() == SKILL.encode()
