from __future__ import annotations

from typing import Any

from loadout.permissions.renderers import (
    RENDERERS,
    JsonSpec,
    TextSpec,
    antigravity_pattern,
    claude_pattern,
    codex_rule,
    opencode_patterns,
    pi_mcp_patterns,
    pi_patterns,
    render_antigravity,
    render_claude,
    render_claude_mcp,
    render_codex,
    render_codex_mcp,
    render_opencode,
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


def test_codex_rule_quotes_each_token_positionally() -> None:
    assert codex_rule("git push", "forbidden") == (
        'prefix_rule(pattern = ["git", "push"], decision = "forbidden")'
    )


def test_codex_maps_categories_to_its_own_decision_names() -> None:
    out = render_codex(Rules(allow=("ls",), deny=("git push",), ask=("heroku",)))
    assert 'pattern = ["ls"], decision = "allow"' in out
    assert 'pattern = ["git", "push"], decision = "forbidden"' in out
    assert 'pattern = ["heroku"], decision = "prompt"' in out


def test_codex_skips_globs_and_lists_them_at_the_end() -> None:
    out = render_codex(Rules(allow=("ls", "docker stop cc-workbench-*")))
    assert 'pattern = ["docker", "stop", "cc-workbench-*"]' not in out
    assert "# Skipped — glob entries Codex's token matcher can't express" in out
    assert "#   docker stop cc-workbench-*" in out


def test_codex_omits_the_skipped_block_when_there_are_no_globs() -> None:
    assert "# Skipped" not in render_codex(Rules(allow=("ls",)))


def test_codex_output_ends_with_a_single_newline() -> None:
    out = render_codex(Rules(allow=("ls",)))
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_codex_mcp_wildcard_deny_disables_the_server() -> None:
    out = render_codex_mcp(Rules(mcp_deny=("jina/*",)))
    assert '[mcp_servers."jina"]' in out
    assert "enabled = false" in out


def test_codex_mcp_wildcard_allow_sets_default_mode() -> None:
    out = render_codex_mcp(Rules(mcp_allow=("jina/*",)))
    assert 'default_tools_approval_mode = "approve"' in out


def test_codex_mcp_per_tool_sections_and_disabled_list() -> None:
    rules = Rules(mcp_allow=("jina/search",), mcp_deny=("jina/write",))
    out = render_codex_mcp(rules)
    assert 'disabled_tools = ["write"]' in out
    assert '[mcp_servers."jina".tools."search"]' in out
    assert 'approval_mode = "approve"' in out


def test_codex_renderers_are_registered_as_text() -> None:
    assert isinstance(RENDERERS["codex"], TextSpec)
    assert isinstance(RENDERERS["codex-mcp"], TextSpec)


def test_antigravity_wraps_entries_in_command_form() -> None:
    assert antigravity_pattern("git status") == "command(git status)"


def test_antigravity_skips_globs_its_matcher_cannot_express() -> None:
    doc = render_antigravity(Rules(allow=("ls", "docker stop cc-workbench-*")), {})
    assert doc["permissions"]["allow"] == ["command(ls)"]


def test_antigravity_appends_mcp_entries_after_shell_entries() -> None:
    rules = Rules(allow=("ls",), mcp_allow=("jina/*",))
    assert render_antigravity(rules, {})["permissions"]["allow"] == [
        "command(ls)",
        "mcp(jina/*)",
    ]


def test_antigravity_emits_categories_in_order_on_an_empty_base() -> None:
    doc = render_antigravity(Rules(allow=("ls",)), {})
    assert list(doc) == ["permissions"]
    assert list(doc["permissions"]) == ["allow", "deny", "ask"]


def test_antigravity_does_not_mutate_its_base() -> None:
    base: dict[str, Any] = {}
    render_antigravity(Rules(allow=("ls",)), base)
    assert base == {}


def test_claude_pattern_appends_colon_star_to_a_prefix() -> None:
    assert claude_pattern("git status") == "Bash(git status:*)"


def test_claude_pattern_keeps_a_glob_literal() -> None:
    assert claude_pattern("docker stop cc-workbench-*") == ("Bash(docker stop cc-workbench-*)")


def test_claude_orders_owned_keys_before_hand_maintained_ones() -> None:
    base = {"permissions": {"defaultMode": "auto"}}
    doc = render_claude(Rules(allow=("ls",)), base)
    assert list(doc["permissions"]) == ["allow", "deny", "ask", "defaultMode"]


def test_claude_keeps_the_permissions_key_at_its_position_in_the_base() -> None:
    base = {"$schema": "x", "env": {}, "permissions": {"defaultMode": "auto"}, "model": "y"}
    doc = render_claude(Rules(allow=("ls",)), base)
    assert list(doc) == ["$schema", "env", "permissions", "model"]


def test_claude_concatenates_shell_then_mcp_then_extras() -> None:
    rules = Rules(
        allow=("ls",),
        mcp_allow=("jina/search",),
        claude_extra_allow=("WebSearch",),
    )
    doc = render_claude(rules, {"permissions": {}})
    assert doc["permissions"]["allow"] == [
        "Bash(ls:*)",
        "mcp__jina__search",
        "WebSearch",
    ]


def test_claude_ask_has_no_extras_channel() -> None:
    rules = Rules(ask=("heroku",), mcp_ask=("jina/x",))
    doc = render_claude(rules, {"permissions": {}})
    assert doc["permissions"]["ask"] == ["Bash(heroku:*)", "mcp__jina__x"]


def test_claude_with_empty_rules_empties_all_three_lists() -> None:
    base = {"permissions": {"defaultMode": "auto"}}
    doc = render_claude(EMPTY_RULES, base)
    assert doc["permissions"] == {
        "allow": [],
        "deny": [],
        "ask": [],
        "defaultMode": "auto",
    }


def test_claude_does_not_mutate_its_base() -> None:
    base: dict[str, Any] = {"permissions": {"defaultMode": "auto"}}
    render_claude(Rules(allow=("ls",)), base)
    assert base == {"permissions": {"defaultMode": "auto"}}


def test_claude_discards_stale_generated_keys_from_a_base() -> None:
    base = {"permissions": {"allow": ["STALE"], "defaultMode": "auto"}}
    doc = render_claude(Rules(allow=("ls",)), base)
    assert doc["permissions"]["allow"] == ["Bash(ls:*)"]
    assert list(doc["permissions"]) == ["allow", "deny", "ask", "defaultMode"]


def test_claude_never_reads_a_file() -> None:
    """The base is a parameter; rendering must work with no filesystem at all."""
    doc = render_claude(Rules(allow=("ls",)), {})
    assert doc["permissions"]["allow"] == ["Bash(ls:*)"]


def test_opencode_emits_both_bare_and_argument_forms() -> None:
    assert opencode_patterns("pwd") == ["pwd", "pwd *"]


def test_opencode_keeps_a_glob_literal() -> None:
    assert opencode_patterns("docker stop cc-workbench-*") == ["docker stop cc-workbench-*"]


def test_opencode_seeds_the_bash_catch_all_first() -> None:
    doc = render_opencode(Rules(allow=("pwd",)), {})
    assert next(iter(doc["permission"]["bash"])) == "*"


def test_opencode_emits_deny_after_allow_for_last_match_wins() -> None:
    bash = render_opencode(Rules(allow=("git",), deny=("git",)), {})["permission"]["bash"]
    assert bash["git"] == "deny"


def test_opencode_mcp_entries_become_top_level_permission_keys() -> None:
    doc = render_opencode(Rules(mcp_allow=("jina/*",)), {})
    assert doc["permission"]["jina_*"] == "allow"


def test_opencode_extra_toggles_are_emitted_verbatim() -> None:
    rules = Rules(opencode_extra={"webfetch": "allow", "skill": "allow"})
    permission = render_opencode(rules, {})["permission"]
    assert permission["webfetch"] == "allow"
    assert permission["skill"] == "allow"


def test_opencode_appends_permission_after_the_base_keys() -> None:
    base = {"$schema": "x", "model": "m", "provider": {}}
    doc = render_opencode(Rules(allow=("pwd",)), base)
    assert list(doc) == ["$schema", "model", "provider", "permission"]


def test_opencode_does_not_reorder_cross_key_entries() -> None:
    """Deliberately unlike Pi — render_opencode assigns in place. Do not harmonise."""
    bash = render_opencode(Rules(allow=("foo", "bar"), deny=("foo",)), {})["permission"]["bash"]
    assert list(bash) == ["*", "foo", "foo *", "bar", "bar *"]


def test_opencode_does_not_mutate_its_base() -> None:
    base: dict[str, Any] = {"$schema": "x"}
    render_opencode(Rules(allow=("pwd",)), base)
    assert base == {"$schema": "x"}
