from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GLOBAL_PRESET", "SliceOutput", "agent_slices", "known_agents"]


@dataclass(frozen=True)
class SliceOutput:
    """Where one slice of one agent's configuration is written.

    `destination` is a template resolved at render time, so a relocated harness
    is followed without editing a manifest — see ADR 0011. `output` is an
    in-repo staged path for the one case that has no destination at all.

    `source_slice` names the slice supplying this one's own content.

    `owned_key` makes this slice a **contributor**: its renderer produces the
    value of that key and the loop assigns it, rather than transforming the
    whole document. Settings is the residual every file starts from, so it is
    never a `source_slice` — spec 1 §3's ownership map, made executable.
    """

    renderer: str | None = None
    destination: str | None = None
    output: str | None = None
    source_slice: str | None = None
    owned_key: str | None = None


# Destinations carry each harness's config-directory variable rather than a
# literal path. The variables differ in name and in kind, and two of OpenCode's
# look right and are not — see docs/reference/README.md, which records how each
# was verified. Keeping them here means a manifest never spells one out.
GLOBAL_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "skills": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}/skills"),
        "instructions": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"),
        "permissions": SliceOutput(
            renderer="claude",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
        ),
        "mcp": SliceOutput(
            renderer="claude-mcp",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/mcp-permissions.json",
        ),
        # Shares settings.json with permissions and the settings residual, so it
        # contributes one key rather than transforming the document.
        "hooks": SliceOutput(
            renderer="claude-hooks",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
            source_slice="hooks",
            owned_key="hooks",
        ),
    },
    "codex": {
        "skills": SliceOutput(destination="${CODEX_HOME:-~/.codex}/skills"),
        "instructions": SliceOutput(destination="${CODEX_HOME:-~/.codex}/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="codex",
            destination="${CODEX_HOME:-~/.codex}/rules/permissions.rules",
        ),
        # Codex is the one harness whose hooks have a file of their own, so this
        # lands without agent-first rendering. Claude's `hooks` key shares
        # settings.json with permissions and settings, which needs two slices to
        # compose into one document — spec 1 §3, not yet built.
        # `hooks` is the only key loadout writes here, so the residual is empty.
        # That is a claim about what loadout writes, not about the file: a
        # hand-made hooks.json may carry keys neither of us anticipated — Cursor's
        # equivalent has a `version` — and this drops them. `preserve` is the
        # mechanism if one is ever identified; none is, so none is named.
        "hooks": SliceOutput(
            renderer="codex-hooks",
            destination="${CODEX_HOME:-~/.codex}/hooks.json",
            source_slice="hooks",
            owned_key="hooks",
        ),
        # The one slice with no destination: sync_config.py reads this staged
        # file and merges it into ~/.codex/config.toml, which holds keys loadout
        # does not own. Spec 4c asks whether that merge survives; until it does
        # not, "staged" is a shape the preset has to express.
        "mcp": SliceOutput(renderer="codex-mcp", output="codex/mcp-permissions.toml"),
    },
    "opencode": {
        "skills": SliceOutput(destination="${XDG_CONFIG_HOME:-~/.config}/opencode/skills"),
        "permissions": SliceOutput(
            renderer="opencode",
            destination="${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json",
        ),
    },
    "pi": {
        "skills": SliceOutput(destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills"),
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
