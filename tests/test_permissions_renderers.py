from __future__ import annotations

from typing import Any

from loadout.permissions.renderers import (
    RENDERERS,
    JsonSpec,
    pi_mcp_patterns,
    pi_patterns,
    render_claude_mcp,
    render_pi,
)
from loadout.permissions.rules import EMPTY_RULES, Rules


def test_claude_mcp_emits_three_categories_in_fixed_order() -> None:
    rules = Rules(mcp_allow=("a/b",), mcp_ask=("c/d",), mcp_deny=("e/f",))
    doc = render_claude_mcp(rules, {})
    assert list(doc) == ["allow", "ask", "deny"]
    assert doc == {"allow": ["a/b"], "ask": ["c/d"], "deny": ["e/f"]}


def test_claude_mcp_serializes_with_ascii_escaping() -> None:
    spec = RENDERERS["claude-mcp"]
    assert isinstance(spec, JsonSpec)
    assert spec.ensure_ascii is True


def test_every_other_json_renderer_keeps_unicode() -> None:
    for name, spec in RENDERERS.items():
        if isinstance(spec, JsonSpec) and name != "claude-mcp":
            assert spec.ensure_ascii is False, name


def test_pi_emits_both_bare_and_argument_forms() -> None:
    assert pi_patterns("pwd") == ["pwd", "pwd *"]


def test_pi_keeps_a_glob_literal() -> None:
    assert pi_patterns("docker stop cc-workbench-*") == ["docker stop cc-workbench-*"]


def test_pi_mcp_patterns_for_a_single_tool() -> None:
    assert pi_mcp_patterns("jina/search") == ["jina_search", "jina:search"]


def test_pi_mcp_patterns_add_server_wide_targets_for_a_wildcard() -> None:
    assert pi_mcp_patterns("jina/*") == [
        "jina_*",
        "jina:*",
        "mcp_server_jina",
        "mcp_connect_jina",
    ]


def test_pi_seeds_catch_alls_first() -> None:
    doc = render_pi(Rules(allow=("pwd",)), {})
    assert next(iter(doc["permission"]["bash"])) == "*"
    assert next(iter(doc["permission"]["mcp"])) == "*"


def test_pi_emits_deny_last_so_it_wins_under_last_match() -> None:
    rules = Rules(allow=("git",), deny=("git",))
    bash = render_pi(rules, {})["permission"]["bash"]
    assert bash["git"] == "deny"
    assert list(bash)[-1] == "git *"


def test_pi_document_shape() -> None:
    doc = render_pi(EMPTY_RULES, {})
    assert list(doc) == ["$schema", "permission"]
    assert list(doc["permission"]) == ["*", "bash", "mcp"]
    assert doc["permission"]["*"] == "allow"


def test_pi_reorders_cross_key_entries_so_later_category_wins_position() -> None:
    rules = Rules(allow=("foo", "bar"), deny=("foo",))
    bash = render_pi(rules, {})["permission"]["bash"]
    assert list(bash) == ["*", "bar", "bar *", "foo", "foo *"]


def test_renderers_are_pure() -> None:
    rules = Rules(allow=("pwd",), mcp_allow=("jina/*",))
    base: dict[str, Any] = {}
    assert render_pi(rules, base) == render_pi(rules, base)
    assert render_claude_mcp(rules, base) == render_claude_mcp(rules, base)
    assert base == {}
