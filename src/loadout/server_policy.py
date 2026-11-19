from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .errors import LoadoutError
from .permissions.rules import Rules, dedupe, mcp_parts
from .servers import Server
from .skill_policy import aliased_names, parse_alias, refuse_alias_clashes


@dataclass(frozen=True)
class ServerPolicy:
    name: str
    alias: str


def parse_server_policies(raw: object, label: str = "servers") -> tuple[ServerPolicy, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise LoadoutError(f"[{label}] must be a table")
    policies: list[ServerPolicy] = []
    for name, block in raw.items():
        if not isinstance(name, str) or not name:
            raise LoadoutError(f"{label} contains an invalid server name: {name!r}")
        if not isinstance(block, dict):
            raise LoadoutError(f"{label}.{name} must be a table")
        if not block:
            raise LoadoutError(f"{label}.{name} is empty; set 'name', or remove the table")
        unknown_keys = sorted(set(block) - {"name"})
        if unknown_keys:
            raise LoadoutError(
                f"{label}.{name}: unrecognised key(s) {', '.join(unknown_keys)}; "
                "'name' is the only key it accepts"
            )
        alias = parse_alias(block, f"{label}.{name}")
        assert alias is not None
        policies.append(ServerPolicy(name, alias))
    refuse_alias_clashes(label, [(policy.name, policy.alias) for policy in policies])
    return tuple(policies)


def server_renames(
    names: Mapping[str, object], policies: tuple[ServerPolicy, ...]
) -> dict[str, str]:
    return aliased_names("server", names, [(policy.name, policy.alias) for policy in policies])


def aliased_servers(
    servers: Mapping[str, Server], policies: tuple[ServerPolicy, ...]
) -> dict[str, Server]:
    renames = server_renames(servers, policies)
    if not renames:
        return dict(servers)
    return {
        renames.get(name, name): replace(server, name=renames[name]) if name in renames else server
        for name, server in servers.items()
    }


def _renamed(entries: tuple[str, ...], renames: Mapping[str, str]) -> tuple[str, ...]:
    def rename(entry: str) -> str:
        server, tool = mcp_parts(entry)
        return f"{renames[server]}/{tool}" if server in renames else entry

    return tuple(dedupe([rename(entry) for entry in entries]))


def aliased_rules(rules: Rules, renames: Mapping[str, str]) -> Rules:
    if not renames:
        return rules
    return replace(
        rules,
        mcp_allow=_renamed(rules.mcp_allow, renames),
        mcp_deny=_renamed(rules.mcp_deny, renames),
        mcp_ask=_renamed(rules.mcp_ask, renames),
    )
