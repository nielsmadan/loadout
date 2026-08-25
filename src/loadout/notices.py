"""What a render has to say about a source it could not fully honour.

Four reporters existed before this module and none of them reached a user:
`unaddressable` and `unregistered_marketplaces` in the plugins slice,
`unrecognised_events` in hooks, and the adapters' unmapped-event comment, which
was visible only by opening the generated JavaScript. One unreported function is
a footnote; four is a missing surface, and ADR 0015 named that gap rather than
hiding it.

Every notice here is **advisory**. Each of the underlying reports describes a
source that rendered successfully while doing less than it says — a plugin left
switched off, a hook that will never fire — so none of them is drift and none
changes an exit code. Drift means generated output disagrees with its source;
these are cases where the source itself asks for something the harness cannot
carry.

The functions take documents rather than reading them, so the whole module is
pure and the caller supplies machine state (ADR 0001).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .adapters import OPENCODE_MAPPINGS, PI_MAPPINGS
from .hooks import CLAUDE_EVENTS, CODEX_EVENTS, unrecognised_events
from .permissions.renderers import CATCH_ALL_RENDERERS
from .permissions.rules import Rules
from .plugins import ADDRESSED_BY, unaddressable, unregistered_marketplaces
from .servers import Server

__all__ = [
    "Notice",
    "known_events",
    "notices_for",
    "opencode_skills_race",
    "unpermitted_servers",
    "unreached_catch_all",
]

# Where each shell-carrying renderer that is *not* a catch-all carrier keeps its
# catch-all instead. Keyed by renderer, not agent: `pi` authors one and
# `pi-project` does not. A renderer absent here states no shell rules at all
# (the MCP-only ones), so the key was never its to carry and there is nothing to
# report.
CATCH_ALL_ELSEWHERE = {
    "claude": "Claude's is permissions.defaultMode, hand-maintained through the settings slice",
    "claude-project": "Claude's is permissions.defaultMode, hand-maintained through the settings slice",
    "codex": "Codex's is approval_policy in config.toml, which loadout does not write",
    "codex-project": "Codex's is approval_policy in config.toml, which loadout does not write",
    "pi-project": "Pi emits no catch-all at project scope",
}

# OpenCode scans `.claude/skills` as well as its own directory and keys the result
# by skill name, so writing the same name to both leaves which copy survives to a
# race — `skill/index.ts` warns on a duplicate and assigns anyway, under
# `concurrency: "unbounded"`. Either variable removes the Claude directories from
# the scan; `disableClaudeCodeSkills` is `broad || direct`, so checking only the
# specific one would report a collision for anyone who set the broad switch.
# `OPENCODE_DISABLE_EXTERNAL_SKILLS` also works and is deliberately not accepted
# here: it drops `.agents` too, which loadout does not write and the user may.
OPENCODE_SKILL_FLAGS = ("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE")

# Effect's `Config.boolean` parses these; its full accepted set could not be
# verified from the checkout (no node_modules), so an unusual spelling costs one
# advisory line and never an exit code.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def opencode_skills_race(environ: Mapping[str, str]) -> bool:
    """Whether OpenCode would resolve loadout's skills by race rather than by name.

    Machine state, so a caller supplies it (ADR 0001) — and it is read for a
    *notice*, never for generated content, so ADR 0008 is untouched.
    """
    return not any(
        environ.get(flag, "").strip().lower() in _TRUTHY for flag in OPENCODE_SKILL_FLAGS
    )


@dataclass(frozen=True)
class Notice:
    """One thing a user would want to know, and where it came from."""

    agent: str
    slice: str
    message: str

    def render(self) -> str:
        return f"{self.agent}.{self.slice}: {self.message}"


# For Claude and Codex an event outside this set may still be honoured — both
# lists are lower bounds, which is why `unrecognised_events` reports rather than
# filters. For OpenCode and Pi the set is loadout's own translation table, so an
# event outside it provably does not fire: there is no adapter branch to run it.
_ADAPTED = {
    "opencode": frozenset(m.event for m in OPENCODE_MAPPINGS),
    "pi": frozenset(m.event for m in PI_MAPPINGS),
}


def known_events(agent: str) -> frozenset[str]:
    if agent == "claude":
        return CLAUDE_EVENTS
    if agent == "codex":
        return CODEX_EVENTS
    return _ADAPTED.get(agent, frozenset())


def unreached_catch_all(agent: str, renderer: str, verdict: str) -> tuple[Notice, ...]:
    """What a stated `[shell] default` does not reach, said out loud at sync time.

    The docs record the split, but a source that states a catch-all and gets it on
    two harnesses of four is the exact shape ADR 0015 named: rendered successfully,
    doing less than it says, with generated files that look untouched. It matters
    more here than for the advisory slices the other notices cover — permissions is
    the enforcing one, and the direction of the gap is toward *wider* than asked.
    """
    if renderer in CATCH_ALL_RENDERERS or renderer not in CATCH_ALL_ELSEWHERE:
        return ()
    return (
        Notice(
            agent=agent,
            slice="permissions",
            message=(
                f'[shell] default = "{verdict}" is not rendered here — '
                f"{CATCH_ALL_ELSEWHERE[renderer]}; "
                f"see docs/reference/README.md#the-catch-all-default"
            ),
        ),
    )


def unpermitted_servers(servers: Mapping[str, Server], rules: Rules) -> tuple[str, ...]:
    """Servers defined but named by no tool policy, in declaration order.

    Two files, two edits: define the server, then allow its tools. Forgetting the
    second gives a server whose tools are all denied and no error anywhere. This
    is the locality benefit of one section, without giving one file two merge
    algebras.
    """
    named = {
        entry.split("/", 1)[0] for entry in (*rules.mcp_allow, *rules.mcp_ask, *rules.mcp_deny)
    }
    return tuple(name for name in servers if name not in named)


def notices_for(
    agent: str,
    slice_name: str,
    document: Mapping[str, Any],
    known_marketplaces: frozenset[str] = frozenset(),
) -> tuple[Notice, ...]:
    """Everything worth reporting about one agent's slice content."""
    found: list[Notice] = []

    def add(message: str) -> None:
        found.append(Notice(agent=agent, slice=slice_name, message=message))

    if slice_name == "hooks":
        adapted = agent in _ADAPTED
        for event in unrecognised_events(document, known_events(agent)):
            add(
                f"{event} has no adapter mapping, so it will not fire"
                if adapted
                else f"{event} is not in this harness's known event list"
            )

    if slice_name == "plugins":
        for skipped in unaddressable(document, agent):
            add(f"{skipped} — left switched off")
        if ADDRESSED_BY.get(agent) == "marketplace":
            for name in unregistered_marketplaces(document, known_marketplaces):
                add(f"marketplace {name!r} is not registered; the plugin will not resolve yet")

    return tuple(found)
