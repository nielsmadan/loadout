"""init leaves a conflicting item out, and the /loadout skill lands a merged one (ADR 0030).

Two harnesses holding different copies of one skill, or a project whose CLAUDE.md and
AGENTS.md disagree, used to refuse the whole adoption. init now adopts everything else,
leaves both originals untouched and reports the item. The skill writes the merged item
into the source; the next sync refuses over the originals, and `--force` replaces them.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

CLAUDE_SKILL = "---\nname: review\ndescription: Review.\n---\nReview as Claude does.\n"
CODEX_SKILL = "---\nname: review\ndescription: Review.\n---\nReview as Codex does.\n"
MERGED_SKILL = "---\nname: review\ndescription: Review.\n---\nReview carefully.\n"


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _json(out: str) -> dict[str, object]:
    document, _ = json.JSONDecoder().raw_decode(out)
    return document


def test_a_conflicting_skill_is_reported_and_its_originals_untouched(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    claude = _write(fake_home / ".claude/skills/review/SKILL.md", CLAUDE_SKILL)
    codex = _write(fake_home / ".codex/skills/review/SKILL.md", CODEX_SKILL)
    source = tmp_path / "source"
    source.mkdir()
    argv = ["init", "global", str(source), "--harness", "claude", "--harness", "codex"]

    assert loadout.main([*argv, "--yes", "--json"]) == 0

    assert _json(capsys.readouterr().out)["unadopted"] == [
        {
            "kind": "skill",
            "name": "review",
            "originals": [
                {"path": str(claude.parent), "harnesses": ["claude"]},
                {"path": str(codex.parent), "harnesses": ["codex"]},
            ],
        }
    ]
    assert claude.read_text() == CLAUDE_SKILL
    assert codex.read_text() == CODEX_SKILL


def test_a_merged_skill_lands_through_the_guard(tmp_path: Path, fake_home: Path, capsys) -> None:
    claude = _write(fake_home / ".claude/skills/review/SKILL.md", CLAUDE_SKILL)
    codex = _write(fake_home / ".codex/skills/review/SKILL.md", CODEX_SKILL)
    source = tmp_path / "source"
    source.mkdir()
    argv = ["init", "global", str(source), "--harness", "claude", "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 0
    _write(source / "loadout/skills/review/SKILL.md", MERGED_SKILL)
    capsys.readouterr()

    assert loadout.main(["sync", "--global"]) == 1

    # The copy left in place was never loadout's: nothing records it (review 4, batch 4.3).
    assert f"{claude} was modified outside loadout (unknown)" in capsys.readouterr().err
    assert claude.read_text() == CLAUDE_SKILL
    assert loadout.main(["sync", "--global", "--force"]) == 0
    assert "Review carefully." in claude.read_text()
    assert "Review carefully." in codex.read_text()
    assert loadout.main(["check", "--global"]) == 0


def _project(root: Path) -> Path:
    _write(root / "CLAUDE.md", "Claude rules.\n")
    _write(root / "AGENTS.md", "Agents rules.\n")
    _write(root / ".claude/settings.json", '{"permissions": {"allow": ["Bash(ls)"]}}\n')
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def test_disagreeing_project_instructions_are_reported_and_the_rest_adopted(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "codex"]

    assert loadout.main([*argv, "--yes"]) == 0

    out = capsys.readouterr().out
    assert "Not adopted, conflicting copies of instructions 'main':" in out
    assert f"  {root / 'CLAUDE.md'} (claude)" in out
    assert f"  {root / 'AGENTS.md'} (codex)" in out
    assert 'allow = ["=ls"]' in (root / "loadout/permissions.toml").read_text()
    assert (root / "CLAUDE.md").read_text() == "Claude rules.\n"
    assert (root / "AGENTS.md").read_text() == "Agents rules.\n"
    git = ["git", "-C", str(root)]
    tracked = subprocess.run(
        [*git, "ls-files", "CLAUDE.md", "AGENTS.md"], capture_output=True, check=True
    )
    assert tracked.stdout.decode().splitlines() == ["AGENTS.md", "CLAUDE.md"]
    ignored = subprocess.run(
        [*git, "check-ignore", "CLAUDE.md", "AGENTS.md"], capture_output=True, check=False
    )
    assert (ignored.returncode, ignored.stdout) == (1, b"")


def test_merged_project_instructions_land_through_the_guard(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "codex"]
    assert loadout.main([*argv, "--yes"]) == 0
    _write(root / "loadout/instructions/main.md", "Merged rules.\n")
    config = root / "loadout/config.toml"
    config.write_text(config.read_text() + 'instructions = ["main"]\n')
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert "AGENTS.md was modified outside loadout (unknown)" in err
    assert (root / "AGENTS.md").read_text() == "Agents rules.\n"
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    assert (root / "AGENTS.md").read_text().endswith("Merged rules.\n")
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text()
    assert loadout.main(["check", "--root", str(root)]) == 0
