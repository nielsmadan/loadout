"""A stealth source for Devin: its always-on rule, and a skill Devin also reaches through
Claude's folder."""

from __future__ import annotations

from pathlib import Path

from loadout.stealth_check import Machine, TargetView, target_notes


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


def test_a_skill_devin_also_reads_in_claude_s_folder_is_a_note() -> None:
    outputs = (
        "CLAUDE.local.md",
        ".claude/skills/probe/SKILL.md",
        ".devin/rules/app.md",
        ".devin/skills/probe/SKILL.md",
    )
    assert target_notes(_view(outputs), MACHINE, ("claude", "devin")) == (
        (
            "/t: Devin also reads .claude/skills while its claude import is on (the default), "
            "so the skill probe reaches it twice"
        ),
    )
