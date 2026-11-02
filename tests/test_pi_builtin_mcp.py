"""Pi renders for its built-in MCP (Pi 1.4.0), not for pi-mcp-adapter.

The built-in reads `~/.pi/agent/mcp.json` and `.pi/mcp.json`, ignores `bearerTokenEnv`, and names
each tool `mcp__<server>__<tool>` with every character outside `[A-Za-z0-9_]` replaced by `_`
(`dist/extensions/mcp/tools.js` `createMcpToolName`). pi-permission-system matches such a tool by
its name against the top-level keys of `permission`; its `mcp` map governs only the adapter's
`mcp` proxy tool, so a policy written there matched nothing under the built-in.
"""

from __future__ import annotations

from typing import Any

import pytest

from loadout.errors import LoadoutError
from loadout.extract import extract_pi, extract_pi_project, extract_value
from loadout.permissions.renderers import pi_mcp_key, render_pi, render_pi_project
from loadout.permissions.rules import Rules
from loadout.servers import Server, render_pi_servers


def _http(name: str, **fields: Any) -> Server:
    return Server(name=name, transport="http", url="https://x.test/mcp", **fields)


# --- servers --------------------------------------------------------------------------


def test_http_auth_renders_as_an_authorization_header_pi_expands() -> None:
    server = _http("docs", auth_env_var="DOCS_TOKEN", headers={"X-Team": "core"})
    assert render_pi_servers({"docs": server}) == {
        "mcpServers": {
            "docs": {
                "url": "https://x.test/mcp",
                "headers": {"Authorization": "Bearer ${DOCS_TOKEN}", "X-Team": "core"},
            }
        }
    }


def test_a_server_name_pi_rejects_is_refused() -> None:
    with pytest.raises(LoadoutError, match=r"pi: MCP server 'svc\.two' is not a name Pi accepts"):
        render_pi_servers({"svc.two": _http("svc.two")})


def test_server_names_differing_only_in_dash_and_underscore_are_refused() -> None:
    servers = {"my-svc": _http("my-svc"), "my_svc": _http("my_svc")}
    with pytest.raises(LoadoutError, match=r"'my-svc' and 'my_svc' are both 'my_svc'"):
        render_pi_servers(servers)


def test_servers_round_trip_through_extraction() -> None:
    servers = {
        "docs": _http("docs", auth_env_var="DOCS_TOKEN", headers={"X-Team": "core"}),
        "local": Server(name="local", transport="stdio", command="srv", args=("-v",)),
    }
    document = render_pi_servers(servers)
    extraction = extract_value("pi-servers", document)
    assert extraction.notes == ()
    assert extraction.value == servers
    assert render_pi_servers(extraction.value) == document


def test_adapter_bearer_auth_is_carried_as_the_header() -> None:
    entry = {"url": "https://x.test/mcp", "auth": "bearer", "bearerTokenEnv": "DOCS_TOKEN"}
    extraction = extract_value("pi-servers", {"mcpServers": {"docs": entry}})
    assert extraction.notes == ()
    assert extraction.value == {"docs": _http("docs", auth_env_var="DOCS_TOKEN")}


def test_bearer_token_env_without_bearer_auth_is_noted_and_not_adopted() -> None:
    entry = {"url": "https://x.test/mcp", "bearerTokenEnv": "DOCS_TOKEN"}
    extraction = extract_value("pi-servers", {"mcpServers": {"docs": entry}})
    assert extraction.value == {"docs": _http("docs")}
    assert [(note.kind, note.text) for note in extraction.notes] == [
        (
            "cannot",
            (
                "pi-servers: docs bearerTokenEnv is not adopted; Pi's built-in MCP ignores it, "
                "and pi-mcp-adapter reads it only beside auth: bearer"
            ),
        )
    ]


def test_an_authorization_header_beside_adapter_bearer_auth_is_refused() -> None:
    entry = {
        "url": "https://x.test/mcp",
        "auth": "bearer",
        "bearerTokenEnv": "DOCS_TOKEN",
        "headers": {"Authorization": "Bearer abc"},
    }
    extraction = extract_value("pi-servers", {"mcpServers": {"docs": entry}})
    assert [note.kind for note in extraction.notes] == ["lost"]
    assert "Authorization beside auth: bearer" in extraction.notes[0].detail


def test_adapter_oauth_is_noted_because_the_built_in_signs_in_itself() -> None:
    entry = {"url": "https://x.test/mcp", "auth": "oauth"}
    extraction = extract_value("pi-servers", {"mcpServers": {"docs": entry}})
    assert extraction.value == {"docs": _http("docs")}
    assert [note.kind for note in extraction.notes] == ["cannot"]


# --- tool names -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entry", "key"),
    [
        ("docs/search", "mcp__docs__search"),
        ("my-svc/read.all", "mcp__my_svc__read_all"),
        ("docs/*", "mcp__docs__*"),
        ("docs/re?d*", "mcp__docs__re?d*"),
    ],
)
def test_a_target_becomes_the_tool_name_pi_gives_it(entry: str, key: str) -> None:
    assert pi_mcp_key(entry) == key


# --- policy ---------------------------------------------------------------------------


def test_global_policy_is_top_level_tool_keys_after_an_mcp_catch_all() -> None:
    rules = Rules(mcp_allow=("docs/*",), mcp_deny=("docs/drop",), mcp_ask=("web/fetch",))
    assert render_pi(rules, {})["permission"] == {
        "*": "allow",
        "bash": {"*": "ask"},
        "mcp__*": "ask",
        "mcp__docs__*": "allow",
        "mcp__web__fetch": "ask",
        "mcp__docs__drop": "deny",
    }


def test_a_target_in_two_categories_moves_to_the_later_one() -> None:
    rules = Rules(mcp_allow=("docs/read", "docs/list"), mcp_deny=("docs/read",))
    keys = list(render_pi(rules, {})["permission"])
    assert keys[-2:] == ["mcp__docs__list", "mcp__docs__read"]
    assert render_pi(rules, {})["permission"]["mcp__docs__read"] == "deny"


def test_project_policy_is_top_level_tool_keys_with_no_catch_all() -> None:
    rules = Rules(mcp_allow=("docs/*",), mcp_deny=("docs/drop",))
    assert render_pi_project(rules, {}) == {
        "permission": {"bash": {}, "mcp__docs__*": "allow", "mcp__docs__drop": "deny"}
    }


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        (
            Rules(mcp_allow=("my-svc/*",), mcp_deny=("my_svc/drop",)),
            r"servers 'my-svc' and 'my_svc' are both 'my_svc'",
        ),
        (
            Rules(mcp_allow=("docs/read.all",), mcp_deny=("docs/read_all",)),
            r"targets 'docs/read\.all' and 'docs/read_all' are both 'mcp__docs__read_all'",
        ),
        (
            Rules(mcp_allow=("a/b__c",), mcp_deny=("a__b/c",)),
            r"targets 'a/b__c' and 'a__b/c' are both 'mcp__a__b__c'",
        ),
    ],
)
def test_targets_pi_cannot_tell_apart_are_refused(rules: Rules, message: str) -> None:
    for render in (render_pi, render_pi_project):
        with pytest.raises(LoadoutError, match=message):
            render(rules, {})


def test_a_tool_name_pi_would_shorten_is_refused() -> None:
    tool = "t" * 60
    with pytest.raises(LoadoutError, match="longer than the 64 characters"):
        render_pi(Rules(mcp_allow=(f"docs/{tool}",)), {})


def test_a_long_wildcard_target_is_rendered() -> None:
    entry = "docs/" + "t" * 60 + "*"
    assert render_pi(Rules(mcp_allow=(entry,)), {})["permission"][pi_mcp_key(entry)] == "allow"


# --- read back ------------------------------------------------------------------------


def test_global_policy_round_trips() -> None:
    rules = Rules(mcp_allow=("docs/*",), mcp_deny=("docs/drop",), mcp_ask=("web/fetch",))
    document = render_pi(rules, {})
    extraction = extract_pi(document)
    assert extraction.notes == ()
    assert extraction.rules.mcp_allow == ("docs/*",)
    assert extraction.rules.mcp_ask == ("web/fetch",)
    assert extraction.rules.mcp_deny == ("docs/drop",)
    assert render_pi(extraction.rules, {}) == document


def test_project_policy_round_trips() -> None:
    rules = Rules(mcp_allow=("docs/*",), mcp_deny=("docs/drop",))
    document = render_pi_project(rules, {})
    extraction = extract_pi_project(document)
    assert extraction.notes == ()
    assert render_pi_project(extraction.rules, {}) == document


def test_an_adapter_policy_map_is_carried_as_tool_keys_and_noted() -> None:
    adapter = {
        "*": "ask",
        "docs_*": "allow",
        "docs:*": "allow",
        "mcp_server_docs": "allow",
        "mcp_connect_docs": "allow",
    }
    document = {"permission": {"*": "allow", "bash": {"*": "ask"}, "mcp": adapter}}
    extraction = extract_pi(document)
    assert extraction.rules.mcp_allow == ("docs/*",)
    assert [(note.kind, note.text) for note in extraction.notes] == [
        (
            "cannot",
            (
                "pi: the mcp map is pi-mcp-adapter's, so its rules are carried as Pi's built-in "
                "MCP tool names; the adapter, which replaces the built-in when installed, is "
                "not governed by them"
            ),
        )
    ]
