from __future__ import annotations

import json
from pathlib import Path

from loadout.composition import HEADER
from loadout.stealth_check import Machine, TargetView, target_notes
from loadout.stealth_manifest import load_stealth_manifest
from loadout.stealth_render import TargetFacts, render_stealth
from test_stealth_render import make_stealth, relative, render

NO_FACTS = TargetFacts(agents_md=False, claude_md_on_path=False)
SERVER = '[svc]\ntransport = "http"\nurl = "https://svc.example/mcp"\n'


def test_cursor_writes_a_rule_named_for_the_stealth_source_its_cli_json_and_its_servers(
    tmp_path: Path,
) -> None:
    folder = make_stealth(
        tmp_path / "ov",
        "\n[cursor]\ninstructions = ['one']\n",
        {
            "instructions/one.md": "First.\n",
            "permissions.toml": (
                '[shell]\nallow = ["ls"]\nask = ["git push"]\ndeny = ["rm -rf"]\n\n'
                '[mcp]\nallow = ["svc/search"]\n'
            ),
            "mcp.toml": SERVER,
        },
    )
    target = tmp_path / "t"
    files = render(folder, target)
    assert relative(files, target) == {
        ".cursor/rules/app.mdc",
        ".cursor/cli.json",
        ".cursor/mcp.json",
    }
    assert files[target / ".cursor/rules/app.mdc"] == (
        f"---\nalwaysApply: true\n---\n\n{HEADER}\n\nFirst.\n"
    )
    assert json.loads(str(files[target / ".cursor/cli.json"])) == {
        "permissions": {"allow": ["Shell(ls)", "Mcp(svc:search)"], "deny": ["Shell(rm -rf)"]}
    }
    assert json.loads(str(files[target / ".cursor/mcp.json"])) == {
        "mcpServers": {"svc": {"url": "https://svc.example/mcp"}}
    }


def test_cursor_takes_instructions_from_the_common_block(tmp_path: Path) -> None:
    folder = make_stealth(
        tmp_path / "ov",
        "\n[all]\ninstructions = ['one']\n\n[cursor]\n[pi]\n",
        {"instructions/one.md": "First.\n"},
    )
    target = tmp_path / "t"
    assert relative(render(folder, target), target) == {
        ".cursor/rules/app.mdc",
        ".pi/APPEND_SYSTEM.md",
    }


def test_cursor_writes_no_cli_json_when_it_would_hold_no_rule(tmp_path: Path) -> None:
    folder = make_stealth(
        tmp_path / "ov",
        "\n[cursor]\n",
        {"permissions.toml": '[shell]\nask = ["git push"]\n', "mcp.toml": SERVER},
    )
    target = tmp_path / "t"
    assert relative(render(folder, target), target) == {".cursor/mcp.json"}


def test_an_ask_cursor_cannot_hold_is_a_render_note(tmp_path: Path) -> None:
    folder = make_stealth(
        tmp_path / "ov",
        "\n[cursor]\n",
        {"permissions.toml": '[shell]\nallow = ["git"]\nask = ["git push"]\n'},
    )
    rendered = render_stealth(load_stealth_manifest(folder), "app", tmp_path / "t", NO_FACTS)
    assert rendered.notes == (
        (
            "cursor: ask 'git push' has no list in Cursor, so it is left to Cursor's "
            "approvalMode, which prompts only in allowlist mode"
        ),
        (
            "cursor: allow 'git' is not rendered because it would also approve something the "
            "source asks about, and Cursor has no ask list to keep that prompting"
        ),
    )
    assert rendered.files == {}


def skill_files(tmp_path: Path, body: str) -> set[str]:
    folder = make_stealth(
        tmp_path / "ov",
        body,
        {
            "skills/probe/SKILL.md": "---\nname: probe\ndescription: d\n---\nBody\n",
            "skills/probe/reference.md": "ref\n",
        },
    )
    target = tmp_path / "t"
    return relative(render(folder, target), target)


def test_a_cursor_skill_lands_in_its_own_folder(tmp_path: Path) -> None:
    assert skill_files(tmp_path, "\n[cursor]\n") == {
        ".cursor/skills/probe/SKILL.md",
        ".cursor/skills/probe/reference.md",
    }


def test_a_skill_identical_for_codex_and_cursor_lands_once_in_agents_skills(
    tmp_path: Path,
) -> None:
    assert skill_files(tmp_path, "\n[codex]\n[cursor]\n") == {
        ".agents/skills/probe/SKILL.md",
        ".agents/skills/probe/reference.md",
    }


def _view(outputs: tuple[str, ...]) -> TargetView:
    return TargetView(
        root=Path("/t"),
        outputs=outputs,
        codex_config=None,
        stealth_opencode={},
        team_opencode={},
        pi_mcp_edited=False,
        branches={},
        carried=(),
        render_notes=(),
    )


MACHINE = Machine(
    codex_file=Path("/h/.codex/config.toml"),
    codex={},
    pi_trust_file=Path("/h/.pi/agent/trust.json"),
    pi_trust={},
    opencode_race=False,
)


def test_cursor_reading_claude_s_files_too_is_a_note() -> None:
    outputs = (
        "CLAUDE.local.md",
        ".claude/skills/probe/SKILL.md",
        ".cursor/rules/app.mdc",
        ".cursor/skills/probe/SKILL.md",
    )
    assert target_notes(_view(outputs), MACHINE, ("claude", "cursor")) == (
        (
            "/t: Cursor also reads CLAUDE.local.md while its third-party imports are on (the "
            "default), so it loads the stealth source's instructions twice"
        ),
        (
            "/t: Cursor also reads .claude/skills while its third-party imports are on (the "
            "default), so the skill probe reaches it twice"
        ),
    )


def test_a_skill_in_agents_skills_and_cursor_skills_is_a_note() -> None:
    outputs = (".agents/skills/probe/SKILL.md", ".cursor/skills/probe/SKILL.md")
    assert target_notes(_view(outputs), MACHINE, ("codex", "cursor")) == (
        (
            "/t: the skill probe is in .agents/skills and in .cursor/skills because its "
            "rendering differs between harnesses, so the harness that reads both sees it twice"
        ),
    )


def test_a_shared_skill_claude_also_gets_reaches_cursor_twice() -> None:
    outputs = ("CLAUDE.local.md", ".agents/skills/probe/SKILL.md", ".claude/skills/probe/SKILL.md")
    assert target_notes(_view(outputs), MACHINE, ("claude", "codex", "cursor")) == (
        (
            "/t: Cursor also reads .claude/skills while its third-party imports are on (the "
            "default), so the skill probe reaches it twice"
        ),
    )
