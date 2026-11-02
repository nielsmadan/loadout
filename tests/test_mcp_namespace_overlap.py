"""A server-scoped MCP rule must not reach another server's tools.

OpenCode names a tool `<server>_<tool>` and Pi `mcp__<server>__<tool>`, and a tool name may
hold the separator itself, so `a/*` is `a_*` in OpenCode and also matches every tool of a
server named `a_b`. Render refuses such a rule, whatever its decision, against every server
the source names: in a rule or in mcp.toml.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_all, render_project
from loadout.errors import LoadoutError
from loadout.permissions.renderers import (
    refuse_mcp_namespace_overlap,
    render_opencode,
    render_pi,
    render_pi_project,
)
from loadout.permissions.rules import Rules
from registration import register


def _mcp_keys(document: dict[str, object]) -> list[str]:
    permission = document["permission"]
    assert isinstance(permission, dict)
    return [key for key in permission if key not in ("*", "bash")]


@pytest.mark.parametrize(
    ("rules", "message"),
    [
        (
            Rules(mcp_allow=("a/*",), mcp_deny=("a_b/x",)),
            (
                r"opencode: MCP rule 'a/\*' is 'a_\*', which also matches tools of server 'a_b'; "
                r"rename 'a' or 'a_b'"
            ),
        ),
        (
            Rules(mcp_deny=("a_b/c",), mcp_allow=("a/x",)),
            r"MCP rule 'a_b/c' is 'a_b_c', which also matches tools of server 'a'",
        ),
        (
            Rules(mcp_allow=("svc/*",), mcp_ask=("svc.two/read",)),
            r"MCP rule 'svc/\*' is 'svc_\*', which also matches tools of server 'svc\.two'",
        ),
        (
            Rules(mcp_allow=("a/?_*",), mcp_deny=("a_b/x",)),
            r"MCP rule 'a/\?_\*' is 'a_\?_\*', which also matches tools of server 'a_b'",
        ),
    ],
)
def test_opencode_refuses_a_rule_that_reaches_another_server(rules: Rules, message: str) -> None:
    with pytest.raises(LoadoutError, match=message):
        render_opencode(rules, {})


def test_opencode_renders_servers_whose_names_only_share_a_start() -> None:
    rules = Rules(mcp_allow=("a/*", "ab/x"), mcp_deny=("a-b/y",))
    assert _mcp_keys(render_opencode(rules, {})) == ["a_*", "ab_x", "a-b_y"]


def test_a_question_mark_stands_for_one_character() -> None:
    refuse_mcp_namespace_overlap("opencode", Rules(mcp_allow=("a/?x",)), ["a_b"])
    with pytest.raises(LoadoutError, match=r"server 'a_b'"):
        refuse_mcp_namespace_overlap("opencode", Rules(mcp_allow=("a/??x",)), ["a_b"])


def test_pi_refuses_a_rule_that_reaches_another_server() -> None:
    rules = Rules(mcp_allow=("a/*",), mcp_deny=("a__b/x",))
    message = (
        r"pi: MCP rule 'a/\*' is 'mcp__a__\*', which also matches tools of server 'a__b'; "
        r"rename 'a' or 'a__b'"
    )
    for render in (render_pi, render_pi_project):
        with pytest.raises(LoadoutError, match=message):
            render(rules, {})


def test_pi_renders_servers_one_underscore_apart() -> None:
    rules = Rules(mcp_allow=("a/*",), mcp_deny=("a_b/x",))
    assert _mcp_keys(render_pi_project(rules, {})) == ["mcp__a__*", "mcp__a_b__x"]


def test_a_rule_naming_servers_by_wildcard_is_not_server_scoped() -> None:
    rules = Rules(mcp_allow=("*/read", "a/x"), mcp_deny=("b/y",))
    assert _mcp_keys(render_opencode(rules, {})) == ["*_read", "a_x", "b_y"]


@pytest.mark.parametrize(
    ("harness", "server", "key"),
    [("opencode", "a_b", "a_\\*"), ("pi", "a__b", "mcp__a__\\*")],
)
def test_a_server_named_only_in_mcp_toml_is_checked(harness: str, server: str, key: str) -> None:
    with pytest.raises(LoadoutError, match=rf"'a/\*' is '{key}', .* server '{server}'"):
        refuse_mcp_namespace_overlap(harness, Rules(mcp_allow=("a/*",)), [server])


def test_a_declared_server_with_its_own_name_passes() -> None:
    refuse_mcp_namespace_overlap("opencode", Rules(mcp_allow=("a/*",)), ["a", "b"])
    assert render_opencode(Rules(mcp_allow=("a/*",)), {})["permission"]["a_*"] == "allow"


def test_global_render_checks_the_servers_mcp_toml_declares(root: Path) -> None:
    register(root)
    with (root / "mcp.toml").open("a", encoding="utf-8") as handle:
        handle.write('\n[svc__extra]\ntransport = "stdio"\ncommand = "srv"\n')
    with pytest.raises(LoadoutError, match=r"'svc/\*' is .* server 'svc__extra'"):
        render_all(root)


@pytest.mark.parametrize(
    ("harness", "server", "key"),
    [("opencode", "a_b", "a_\\*"), ("pi", "a__b", "mcp__a__\\*")],
)
def test_project_render_checks_the_servers_mcp_toml_declares(
    tmp_path: Path, harness: str, server: str, key: str
) -> None:
    source = tmp_path / "loadout"
    source.mkdir()
    (source / "config.toml").write_text(f'harnesses = ["{harness}"]\n', encoding="utf-8")
    (source / "permissions.toml").write_text('[mcp]\nallow = ["a/*"]\n', encoding="utf-8")
    (source / "mcp.toml").write_text(
        f'[a]\ntransport = "stdio"\ncommand = "srv"\n\n[{server}]\ntransport = "stdio"\n'
        'command = "srv"\n',
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match=rf"{harness}: MCP rule 'a/\*' is '{key}'"):
        render_project(tmp_path)
