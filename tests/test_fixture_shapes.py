"""The fixture's reach, guarded.

The expected-output comparison is only as good as the shapes its source carries. A
trimmed fixture would still pass every comparison while quietly covering less, so each
shape docs/reference/coverage.md relies on is asserted here. A failure means the fixture
lost reach — restore the entry rather than deleting the assertion.

Matcher semantics are not tested here; tests/test_permissions_renderers.py owns those.
"""

from __future__ import annotations

from pathlib import Path

from loadout.manifest import MANIFEST_NAME, load_manifest
from loadout.permissions.rules import is_glob, mcp_parts, parse_rules

FIXTURE = Path(__file__).parent / "fixtures" / "permissions.toml"
RULES = parse_rules(FIXTURE)


def test_every_shell_category_is_populated() -> None:
    for category in ("allow", "ask", "deny"):
        assert RULES.shell(category), f"[shell] {category} is empty"


def test_every_mcp_category_is_populated() -> None:
    for category in ("allow", "ask", "deny"):
        assert RULES.mcp(category), f"[mcp] {category} is empty"


def test_a_bare_single_word_command_is_present() -> None:
    """Drives the bare-vs-with-arguments split: Claude's `:*`, both forms on OpenCode/Pi."""
    assert [entry for entry in RULES.allow if " " not in entry and not is_glob(entry)]


def test_a_multi_word_prefix_is_present() -> None:
    """Codex splits on whitespace into positional tokens."""
    assert [entry for entry in RULES.allow if " " in entry and not is_glob(entry)]


def test_a_glob_is_present_so_the_skip_path_runs() -> None:
    """Codex and Antigravity skip these and emit a trailing comment block."""
    assert [entry for entry in RULES.allow if is_glob(entry)]


def test_an_entry_appears_in_two_categories_and_is_not_last() -> None:
    """Pi moves the key to the end, OpenCode leaves it in place — ADR 0006.

    Only observable if a later allow entry follows the shared one.
    """
    shared = set(RULES.allow) & set(RULES.deny)
    assert shared, "no entry is in both allow and deny"
    assert RULES.allow.index(next(iter(shared))) < len(RULES.allow) - 1


def test_both_server_wide_and_per_tool_mcp_entries_are_present() -> None:
    every = RULES.mcp_allow + RULES.mcp_ask + RULES.mcp_deny
    tools = [mcp_parts(entry)[1] for entry in every]
    assert "*" in tools, "no server-wide MCP entry"
    assert [tool for tool in tools if tool != "*"], "no per-tool MCP entry"


def test_a_server_wide_deny_and_a_per_tool_deny_are_both_present() -> None:
    """They take different paths on Codex: enabled = false vs disabled_tools."""
    tools = [mcp_parts(entry)[1] for entry in RULES.mcp_deny]
    assert "*" in tools
    assert [tool for tool in tools if tool != "*"]


def test_an_mcp_server_name_needs_toml_quoting() -> None:
    """Forces the quoted-table-key path in the Codex MCP output."""
    every = RULES.mcp_allow + RULES.mcp_ask + RULES.mcp_deny
    servers = [mcp_parts(entry)[0] for entry in every]
    assert [name for name in servers if not name.replace("-", "").replace("_", "").isalnum()]


def test_both_extras_channels_are_populated() -> None:
    assert RULES.claude_extra_allow
    assert RULES.claude_extra_deny
    assert RULES.opencode_extra


def test_a_destination_is_env_templated() -> None:
    """The expansion path is otherwise reachable only from unit tests. Without a
    templated destination here, the whole-document comparison would keep passing
    while covering none of it."""
    manifest = load_manifest(FIXTURE.parent / MANIFEST_NAME)
    templates = [str(d) for target in manifest.targets for d in target.destinations]
    assert [t for t in templates if "${" in t], "no destination exercises ${...} expansion"
