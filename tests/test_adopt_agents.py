"""init reads existing subagents into one source agent each (S-26).

Copies of one agent in several harnesses become one source agent whose model and effort
maps record what differed; copies whose description or instructions differ are left in
place and reported, for the /loadout skill to merge (D-05).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

CLAUDE = (
    "---\nname: reviewer\ndescription: Reviews a diff.\nmodel: sonnet\neffort: high\n"
    "tools: Read, Grep\n---\n\nReview the diff.\n"
)
CODEX = (
    'name = "reviewer"\ndescription = "Reviews a diff."\nmodel = "gpt-5.5"\n'
    'model_reasoning_effort = "high"\ndeveloper_instructions = """Review the diff.\n"""\n'
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _init_global(tmp_path: Path, *harnesses: str) -> tuple[int, Path]:
    source = tmp_path / "source"
    source.mkdir()
    flags = [arg for harness in harnesses for arg in ("--harness", harness)]
    return loadout.main(["init", "global", str(source), *flags, "--yes", "--json"]), source


def test_one_agent_in_two_harnesses_becomes_one_source_agent(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".claude/agents/reviewer.md", CLAUDE)
    _write(fake_home / ".codex/agents/reviewer.toml", CODEX)

    code, source = _init_global(tmp_path, "claude", "codex")

    assert code == 0
    assert (source / "loadout/agents/reviewer.md").read_text() == (
        "---\n"
        'description: "Reviews a diff."\n'
        "model:\n"
        "  claude: sonnet\n"
        "  codex: gpt-5.5\n"
        "effort: high\n"
        "claude:\n"
        "  tools: Read, Grep\n"
        "---\n\n"
        "Review the diff.\n"
    )
    capsys.readouterr()
    assert loadout.main(["check", "--global"]) == 0


def test_an_agent_from_one_harness_reaches_the_others(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".claude/agents/reviewer.md", CLAUDE)

    code, _ = _init_global(tmp_path, "claude", "pi")

    assert code == 0
    rendered = (fake_home / ".pi/agent/agents/reviewer.md").read_text()
    assert rendered.startswith(
        '---\nname: reviewer\ndescription: "Reviews a diff."\nthinking: high\n---\n'
    )
    assert "Review the diff." in rendered


def test_differing_efforts_become_a_per_harness_map(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".claude/agents/reviewer.md", CLAUDE)
    _write(fake_home / ".codex/agents/reviewer.toml", CODEX.replace('"high"', '"ultra"'))

    code, source = _init_global(tmp_path, "claude", "codex")

    assert code == 0
    assert (
        "effort:\n  claude: high\n  codex: ultra\n"
        in (source / "loadout/agents/reviewer.md").read_text()
    )


def test_copies_with_different_instructions_are_left_out(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    claude = _write(fake_home / ".claude/agents/reviewer.md", CLAUDE)
    codex = _write(
        fake_home / ".codex/agents/reviewer.toml", CODEX.replace("Review the diff.", "Be strict.")
    )

    code, source = _init_global(tmp_path, "claude", "codex")

    assert code == 0
    assert json.loads(capsys.readouterr().out)["unadopted"] == [
        {
            "kind": "agent",
            "name": "reviewer",
            "originals": [
                {"path": str(claude), "harnesses": ["claude"]},
                {"path": str(codex), "harnesses": ["codex"]},
            ],
        }
    ]
    assert not (source / "loadout/agents/reviewer.md").exists()
    assert claude.read_text() == CLAUDE


def test_an_agent_adoption_cannot_carry_stays_in_place_with_a_note(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    agent = _write(
        fake_home / ".codex/agents/docs.toml",
        'name = "docs"\ndescription = "D."\ndeveloper_instructions = "x"\n'
        '[mcp_servers.docs]\ncommand = "docs"\n',
    )
    source = tmp_path / "source"
    source.mkdir()

    code = loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"])

    assert code == 0
    assert f"{agent}: not adopted" in capsys.readouterr().out
    assert agent.is_file()
    assert not (source / "loadout/agents/docs.md").exists()


def test_a_project_agent_is_adopted_and_its_outputs_ignored(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    _write(root / ".claude/agents/reviewer.md", CLAUDE)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--harness", "codex"]

    assert loadout.main([*argv, "--yes"]) == 0

    assert (root / "loadout/agents/reviewer.md").is_file()
    assert (root / ".codex/agents/reviewer.toml").is_file()
    ignored = set((root / ".gitignore").read_text().splitlines())
    assert {"/.claude/agents/reviewer.md", "/.codex/agents/reviewer.toml"} <= ignored
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_a_block_scalar_description_survives_adoption(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2.2: `description: |` used to come back as the bare `|`."""
    _write(
        fake_home / ".claude/agents/reviewer.md",
        "---\nname: reviewer\ndescription: |\n  Reviews a diff.\n  Strictly.\n---\n\nReview.\n",
    )

    code, source = _init_global(tmp_path, "claude")

    assert code == 0
    assert (
        'description: "Reviews a diff.\\nStrictly.\\n"\n'
        in (source / "loadout/agents/reviewer.md").read_text()
    )
