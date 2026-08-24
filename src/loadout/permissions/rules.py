from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..errors import LoadoutError


def dedupe(items: Sequence[str]) -> list[str]:
    """Order-preserving dedupe. Never use set() — emission order is semantic."""
    return list(dict.fromkeys(items))


def is_glob(entry: str) -> bool:
    """A source entry ending in `*` is a glob, not a plain command prefix."""
    return entry.endswith("*")


Decision = Literal["allow", "ask", "deny"]

# Ordered least to most strict. `strictest()` resolves by index, so reordering
# this to match a neighbouring tuple silently changes which verdict wins.
DECISIONS: tuple[Decision, ...] = ("allow", "ask", "deny")

# What a catch-all takes when `[shell] default` says nothing. Stating it changes
# no bytes, so within one tier an unstated default and an explicit "ask" are one
# document — across tiers they differ, because only a stated value votes.
UNSTATED_DEFAULT: Decision = "ask"

# Pi seeds its MCP map with this. It is a separate decision from the shell
# catch-all and only coincidentally the same word: `[shell] default` must not
# move it.
MCP_SEED: Decision = "ask"

# A bare `*` shell entry rendered a catch-all by accident, before `[shell]
# default` could state one. The two spellings resolve by different algebras —
# strictest-wins for the key, last-match-wins for the entry — and on OpenCode
# the entry lands exactly where the seed sits, so no extractor can tell them
# apart. Refused at parse time so only one spelling reaches a renderer.
CATCH_ALL_ENTRY = "*"


def strictest(verdicts: Sequence[Decision]) -> Decision:
    """The strictest of several verdicts. Raises on an empty sequence."""
    return max(verdicts, key=DECISIONS.index)


def mcp_parts(entry: str) -> tuple[str, str]:
    server, separator, tool = entry.partition("/")
    if not separator or not server or not tool:
        raise ValueError(f"invalid MCP target: {entry!r}")
    return server, tool


def mcp_native(entry: str) -> str:
    server, tool = mcp_parts(entry)
    return f"mcp__{server}__{tool}"


@dataclass(frozen=True)
class Rules:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()
    mcp_allow: tuple[str, ...] = ()
    mcp_deny: tuple[str, ...] = ()
    mcp_ask: tuple[str, ...] = ()
    claude_extra_allow: tuple[str, ...] = ()
    claude_extra_deny: tuple[str, ...] = ()
    opencode_extra: Mapping[str, str] = field(default_factory=dict)
    # The verdict for everything no rule matches. None means unstated, which is
    # not the same as "ask"; not every renderer can carry one — see
    # docs/reference/README.md#the-catch-all-default.
    default: Decision | None = None

    @property
    def catch_all(self) -> Decision:
        """The verdict a renderer seeds. Never None, so no renderer needs to know
        that unstated means `ask` — getting that wrong emits `null` into JSON."""
        return self.default if self.default is not None else UNSTATED_DEFAULT

    def shell(self, category: str) -> tuple[str, ...]:
        return {"allow": self.allow, "deny": self.deny, "ask": self.ask}[category]

    def mcp(self, category: str) -> tuple[str, ...]:
        return {
            "allow": self.mcp_allow,
            "deny": self.mcp_deny,
            "ask": self.mcp_ask,
        }[category]


EMPTY_RULES = Rules()


def _entries(block: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = block.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise LoadoutError(f"permissions: {key} must be a list of strings")
    return tuple(dedupe(value))


def _parse_default(block: Mapping[str, Any], path: Path) -> Decision | None:
    value = block.get("default")
    if value is None:
        return None
    if not isinstance(value, str) or value not in DECISIONS:
        raise LoadoutError(f"{path}: shell.default {value!r} must be one of {', '.join(DECISIONS)}")
    return value


def _refuse_catch_all_entry(shell: Mapping[str, Any], path: Path) -> None:
    for category in DECISIONS:
        if CATCH_ALL_ENTRY in _entries(shell, category):
            raise LoadoutError(
                f"{path}: shell.{category} may not contain {CATCH_ALL_ENTRY!r} — "
                f'state the catch-all as `default = "{category}"`'
            )


def _mcp_entries(block: Mapping[str, Any], key: str, path: Path) -> tuple[str, ...]:
    entries = _entries(block, key)
    for entry in entries:
        try:
            mcp_parts(entry)
        except ValueError as error:
            raise LoadoutError(f"{path}: mcp.{key}: {error}") from error
    return entries


def parse_rules(path: Path) -> Rules:
    if not path.is_file():
        raise LoadoutError(f"permissions source not found: {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error

    shell = data.get("shell", {})
    mcp = data.get("mcp", {})
    claude_extra = data.get("claude", {}).get("extra", {})
    opencode_extra = data.get("opencode", {}).get("extra", {})
    if not isinstance(opencode_extra, dict) or not all(
        isinstance(v, str) for v in opencode_extra.values()
    ):
        raise LoadoutError(f"{path}: [opencode.extra] must map strings to strings")

    _refuse_catch_all_entry(shell, path)

    return Rules(
        allow=_entries(shell, "allow"),
        deny=_entries(shell, "deny"),
        ask=_entries(shell, "ask"),
        mcp_allow=_mcp_entries(mcp, "allow", path),
        mcp_deny=_mcp_entries(mcp, "deny", path),
        mcp_ask=_mcp_entries(mcp, "ask", path),
        claude_extra_allow=_entries(claude_extra, "allow"),
        claude_extra_deny=_entries(claude_extra, "deny"),
        opencode_extra=dict(opencode_extra),
        default=_parse_default(shell, path),
    )
