"""OpenCode names an MCP tool `<server>_<tool>` after replacing every character outside
`[a-zA-Z0-9_-]` with `_` in both halves (`mcp/catalog.ts` `sanitize` and `toolName`, v1.18.15;
`tool/mcp.ts:22-23` in v2.2.0). A rule keyed by the raw name, `svc.two_read`, matched no tool.
"""

from __future__ import annotations

import pytest

from loadout.errors import LoadoutError
from loadout.extract import extract_opencode
from loadout.permissions.renderers import render_opencode
from loadout.permissions.rules import Rules


def _permission(rules: Rules) -> dict[str, str]:
    permission: dict[str, str] = render_opencode(rules, {})["permission"]
    return {key: value for key, value in permission.items() if key != "bash"}


def test_server_and_tool_names_are_sanitised_as_opencode_names_its_tools() -> None:
    rules = Rules(mcp_allow=("svc.two/read",), mcp_deny=("my server/drop table",))
    assert _permission(rules) == {"svc_two_read": "allow", "my_server_drop_table": "deny"}


def test_wildcards_survive_sanitising() -> None:
    rules = Rules(mcp_allow=("svc.two/*",), mcp_ask=("svc.two/re?d",))
    assert _permission(rules) == {"svc_two_*": "allow", "svc_two_re?d": "ask"}


def test_a_name_opencode_leaves_alone_renders_unchanged() -> None:
    assert _permission(Rules(mcp_allow=("my-svc/read_all",))) == {"my-svc_read_all": "allow"}


def test_two_servers_one_opencode_name_are_refused() -> None:
    rules = Rules(mcp_allow=("svc.two/*",), mcp_deny=("svc_two/purge",))
    with pytest.raises(LoadoutError, match=r"'svc\.two' and 'svc_two' are both 'svc_two'"):
        render_opencode(rules, {})


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        (
            Rules(mcp_allow=("svc/read.all",), mcp_deny=("svc/read_all",)),
            r"targets 'svc/read\.all' and 'svc/read_all' are both 'svc_read_all'",
        ),
        (
            Rules(mcp_allow=("a_b/c",), mcp_deny=("a/b_c",)),
            r"targets 'a_b/c' and 'a/b_c' are both 'a_b_c'",
        ),
    ],
)
def test_two_targets_one_opencode_name_are_refused(rules: Rules, message: str) -> None:
    with pytest.raises(LoadoutError, match=message):
        render_opencode(rules, {})


def test_one_target_in_two_categories_is_not_a_collision() -> None:
    rules = Rules(mcp_allow=("svc.two/read",), mcp_deny=("svc.two/read",))
    assert _permission(rules) == {"svc_two_read": "deny"}


def test_a_sanitised_name_reads_back_as_opencode_names_it_and_renders_the_same_bytes() -> None:
    document = render_opencode(Rules(mcp_allow=("svc.two/read",), mcp_deny=("svc.two/*",)), {})
    extraction = extract_opencode(document)
    assert extraction.rules.mcp_allow == ("svc_two/read",)
    assert extraction.rules.mcp_deny == ("svc_two/*",)
    assert render_opencode(extraction.rules, extraction.base) == document
