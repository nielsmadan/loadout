from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import LoadoutError


def dedupe(items: Sequence[str]) -> list[str]:
    """Order-preserving dedupe. Never use set() — emission order is semantic."""
    return list(dict.fromkeys(items))


def is_glob(entry: str) -> bool:
    """A source entry ending in `*` is a glob, not a plain command prefix."""
    return entry.endswith("*")


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
    )
