"""What sits in a skills or agents folder without being a skill or an agent.

A loose file, or a folder holding no SKILL.md, is not a skill, so init leaves it where it is
rather than adopting it into source nothing renders. `check` reports it only where a harness
reading the folder would load it: each harness's rule is in docs/reference/config.md.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import loadout
from registration import register

SKILL = "---\nname: {name}\ndescription: {name}.\n---\nBody.\n"
README = "# Skills\n\nOne folder per skill.\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _repository(root: Path) -> Path:
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def _kinds(code_and_out: tuple[int, str]) -> tuple[int, dict[str, str]]:
    code, out = code_and_out
    return code, {entry["path"]: entry["kind"] for entry in json.loads(out)["drift"]}


def _check(args: list[str], capsys) -> tuple[int, dict[str, str]]:
    capsys.readouterr()
    code = loadout.main(["check", *args, "--json"])
    return _kinds((code, capsys.readouterr().out))


@pytest.mark.migration_integration
@pytest.mark.parametrize(
    "extra",
    [
        # claude-code-showcase: a README beside the skills.
        {".claude/skills/README.md": README},
        # A folder of shared helpers that is not a skill.
        {".claude/skills/shared/helpers.md": "Shared.\n", ".claude/skills/shared/run.sh": "true\n"},
    ],
    ids=["loose-readme", "folder-without-skill"],
)
def test_a_project_skills_folder_entry_that_is_no_skill_stays_and_passes_check(
    tmp_path: Path, capsys, extra: dict[str, str]
) -> None:
    root = tmp_path / "project"
    _write(root / ".claude/skills/review/SKILL.md", SKILL.format(name="review"))
    for relative, content in extra.items():
        _write(root / relative, content)
    _repository(root)

    init = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(init) == 0

    assert {relative: (root / relative).read_text() for relative in extra} == extra
    assert sorted(p.relative_to(root).as_posix() for p in (root / "loadout").rglob("*.md")) == [
        "loadout/skills/review/SKILL.md"
    ]
    assert _check(["--root", str(root)], capsys) == (0, {})


@pytest.mark.migration_integration
def test_a_global_agents_readme_and_skills_readme_stay_and_pass_check(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """anaiis-dotfiles: Claude skips an agent file without frontmatter."""
    claude = fake_home / ".claude"
    _write(claude / "skills/graphify/SKILL.md", SKILL.format(name="graphify"))
    readmes = {
        claude / "skills/README.md": README,
        claude / "agents/README.md": "# Agents\n\nNamed agents live here.\n",
    }
    for path, content in readmes.items():
        _write(path, content)
    source = tmp_path / "agent-config"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0

    assert {path: path.read_text() for path in readmes} == readmes
    assert _check(["--global"], capsys) == (0, {})


@pytest.mark.migration_integration
def test_a_folder_left_linked_after_a_global_delink_passes_check(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """integralist: ~/.claude/skills links to ~/.agents/skills, whose `shared` folder is no
    skill; init de-links the skills folder and links `shared` back into the original."""
    shared = fake_home / ".agents/skills"
    _write(shared / "review/SKILL.md", SKILL.format(name="review"))
    _write(shared / "shared/conventions.md", "Shared conventions.\n")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/skills").symlink_to(shared)
    source = tmp_path / "agent-config"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0

    left = fake_home / ".claude/skills/shared"
    assert (left.is_symlink(), os.readlink(left)) == (True, str(shared / "shared"))
    assert _check(["--global"], capsys) == (0, {})


def _global_source(root: Path, harnesses: tuple[str, ...]) -> Path:
    blocks = "".join(f"\n[{harness}]\n" for harness in harnesses)
    _write(root / "loadout.toml", f'[[source]]\nname = "test"\npath = "."\n{blocks}')
    _write(root / "skills/review/SKILL.md", SKILL.format(name="review"))
    return register(root)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("harness", "relative"),
    [
        # OpenCode loads every root .md file in a skills folder as a skill.
        ("opencode", ".config/opencode/skills/README.md"),
        # Codex finds a SKILL.md at any depth below a folder that has none of its own.
        ("codex", ".codex/skills/group/nested/SKILL.md"),
        # Droid loads a droid file without frontmatter, named after the file.
        ("droid", ".factory/droids/README.md"),
    ],
)
def test_an_entry_a_harness_would_load_is_still_flagged_in_its_folder(
    tmp_path: Path, fake_home: Path, capsys, harness: str, relative: str
) -> None:
    source = _global_source(tmp_path / "source", (harness,))
    assert loadout.main(["sync", "--root", str(source)]) == 0
    _write(fake_home / relative, README)
    flagged = fake_home / relative
    if harness == "codex":
        flagged = fake_home / ".codex/skills/group"

    assert _check(["--global"], capsys) == (1, {str(flagged): "unmanaged"})


@pytest.mark.integration
def test_a_folder_pi_would_read_markdown_from_is_flagged_in_the_shared_skills_folder(
    tmp_path: Path, capsys
) -> None:
    """Pi collects .md files from a folder without SKILL.md in `.agents/skills`."""
    root = tmp_path / "project"
    _write(root / "loadout/config.toml", 'harnesses = ["codex"]\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _write(root / ".agents/skills/shared/notes.md", "---\ndescription: n\n---\nNotes.\n")
    _write(root / ".agents/skills/scripts/run.sh", "true\n")

    assert _check(["--root", str(root)], capsys) == (
        1,
        {str(root / ".agents/skills/shared"): "unmanaged"},
    )


@pytest.mark.integration
def test_a_skill_reached_through_a_link_inside_a_folder_is_still_flagged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The scanners follow links, so a folder whose SKILL.md is behind one holds a skill."""
    source = _global_source(tmp_path / "source", ("codex",))
    assert loadout.main(["sync", "--root", str(source)]) == 0
    elsewhere = _write(tmp_path / "elsewhere/lint/SKILL.md", SKILL.format(name="lint")).parent
    group = fake_home / ".codex/skills/group"
    group.mkdir()
    (group / "lint").symlink_to(elsewhere)

    assert _check(["--global"], capsys) == (1, {str(group): "unmanaged"})
