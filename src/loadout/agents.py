from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GLOBAL_PRESET", "SliceOutput", "agent_slices", "known_agents"]


@dataclass(frozen=True)
class SliceOutput:
    """Where one slice of one agent's configuration is written.

    `destination` is a template resolved at render time, so a relocated harness
    is followed without editing a manifest — see ADR 0011. `output` is an
    in-repo staged path for the one case that has no destination at all.
    """

    renderer: str | None = None
    destination: str | None = None
    output: str | None = None


# Destinations carry each harness's config-directory variable rather than a
# literal path. The variables differ in name and in kind, and two of OpenCode's
# look right and are not — see docs/reference/README.md, which records how each
# was verified. Keeping them here means a manifest never spells one out.
GLOBAL_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "instructions": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"),
        "permissions": SliceOutput(
            renderer="claude",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
        ),
        "mcp": SliceOutput(
            renderer="claude-mcp",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/mcp-permissions.json",
        ),
    },
    "codex": {
        "instructions": SliceOutput(destination="${CODEX_HOME:-~/.codex}/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="codex",
            destination="${CODEX_HOME:-~/.codex}/rules/permissions.rules",
        ),
        # The one slice with no destination: sync_config.py reads this staged
        # file and merges it into ~/.codex/config.toml, which holds keys loadout
        # does not own. Spec 4c asks whether that merge survives; until it does
        # not, "staged" is a shape the preset has to express.
        "mcp": SliceOutput(renderer="codex-mcp", output="codex/mcp-permissions.toml"),
    },
    "opencode": {
        "permissions": SliceOutput(
            renderer="opencode",
            destination="${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json",
        ),
    },
    "pi": {
        "instructions": SliceOutput(destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="pi",
            destination=(
                "${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/pi-permission-system/config.json"
            ),
        ),
    },
}


def known_agents() -> frozenset[str]:
    return frozenset(GLOBAL_PRESET)


def agent_slices(agent: str) -> dict[str, SliceOutput]:
    return GLOBAL_PRESET[agent]
