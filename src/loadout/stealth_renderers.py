from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import stealth_seams
from .json_paths import JsonPath
from .permissions.renderers import opencode_patterns, render_claude_project, render_droid
from .permissions.rules import Rules
from .servers import Server, render_opencode_servers
from .stealth_documents import OwnedJson
from .stealth_preset import CLAUDE_RULE_LISTS, DROID_RULE_LISTS, setting_keys


def _non_empty_lists(
    document: Mapping[str, Any], names: tuple[str, ...], prefix: JsonPath
) -> tuple[tuple[JsonPath, tuple[str, ...]], ...]:
    return tuple(((*prefix, name), tuple(document[name])) for name in names if document.get(name))


def claude_owned(rules: Rules, fragment: Mapping[str, Any]) -> OwnedJson:
    permissions = render_claude_project(rules, {})["permissions"]
    return OwnedJson(
        keys=setting_keys("claude", fragment),
        rules=_non_empty_lists(permissions, CLAUDE_RULE_LISTS, ("permissions",)),
    )


def droid_owned(rules: Rules, fragment: Mapping[str, Any]) -> OwnedJson:
    lists = render_droid(rules, {})
    return OwnedJson(
        keys=setting_keys("droid", fragment), rules=_non_empty_lists(lists, DROID_RULE_LISTS, ())
    )


def render_opencode_stealth_permission(rules: Rules) -> dict[str, Any]:
    bash: dict[str, str] = {} if rules.default is None else {"*": rules.default}
    for category in ("allow", "deny", "ask"):
        for entry in rules.shell(category):
            for pattern in opencode_patterns(entry):
                bash[pattern] = category
    permission: dict[str, Any] = {"bash": bash}
    for key, category in stealth_seams.opencode_mcp_keys(rules):
        permission[key] = category
    permission.update(rules.opencode_extra)
    return permission


def opencode_owned(
    rules: Rules | None, servers: Mapping[str, Server], instructions: Path | None
) -> OwnedJson:
    keys: list[tuple[JsonPath, Any]] = []
    if rules is not None:
        permission = render_opencode_stealth_permission(rules)
        if permission != {"bash": {}}:
            keys.append((("permission",), permission))
    if servers:
        keys.append((("mcp",), render_opencode_servers(dict(servers))))
    if instructions is not None:
        keys.append((("instructions",), [str(instructions)]))
    return OwnedJson(keys=tuple(keys), rules=())
