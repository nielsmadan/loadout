"""The fixture's reach, guarded.

The expected-output comparison is only as good as the shapes its source carries. A
trimmed fixture would still pass every comparison while quietly covering less, so each
shape docs/reference/coverage.md relies on is asserted here. A failure means the fixture
lost reach — restore the entry rather than deleting the assertion.

Matcher semantics are not tested here; tests/test_permissions_renderers.py owns those.
"""

from __future__ import annotations

from pathlib import Path

from fixture_root import PROJECT_INSTRUCTIONS
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
    """Codex skips these and emits a trailing comment block."""
    assert [entry for entry in RULES.allow if is_glob(entry)]


def test_an_entry_appears_in_two_categories_and_is_not_last() -> None:
    """Provokes deny-wins resolution end to end — merge_rules must drop the
    allow and keep the deny, and OpenCode's entry must move to the deny
    position while Pi's output is unchanged.

    This guarded ADR 0006's key-move until global scope started merging. It no
    longer can: after resolution no entry is in two categories, so render_pi's
    pop-and-reassign never fires through the pipeline. The quirk is pinned by
    test_pi_reorders_cross_key_entries_so_later_category_wins_position, which
    builds Rules directly. See docs/reference/coverage.md.
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


PROJECT_FIXTURES = Path(__file__).parent / "fixtures" / "project"
TEMPLATE_RULES = parse_rules(PROJECT_FIXTURES / "templates" / "web" / "permissions.toml")


def test_the_project_fixture_carries_a_vendored_template() -> None:
    """Without it, the expected project output never proves a template reaches it."""
    assert TEMPLATE_RULES.allow, "the fixture template contributes no allow rule"
    assert TEMPLATE_RULES.deny, "the fixture template contributes no deny rule"
    assert TEMPLATE_RULES.mcp_allow, "the fixture template contributes no MCP entry"


def test_every_template_rule_is_absent_from_the_other_project_tiers() -> None:
    """A rule a project tier already carries would render identically whether or not
    the template merged, so the comparison would pass with templates switched off."""
    committed = parse_rules(PROJECT_FIXTURES / "permissions.toml")
    local = parse_rules(PROJECT_FIXTURES / "permissions.local.toml")
    others = set(
        committed.allow
        + committed.deny
        + committed.ask
        + local.allow
        + local.deny
        + local.ask
        + committed.mcp_allow
        + local.mcp_allow
    )
    template = set(TEMPLATE_RULES.allow + TEMPLATE_RULES.deny + TEMPLATE_RULES.mcp_allow)
    assert not (template & others), f"already in another tier: {sorted(template & others)}"


def test_the_project_fixture_declares_instructions_out_of_sorted_order() -> None:
    """`test_instruction_blocks_appear_in_declared_order_below_the_template` proves
    nothing if the declared order happens to match the directory listing — a render
    that sorted its fragments would pass. Reversing the pair is what gives that test
    something to catch, so it is a property of the fixture, not of the assertion."""
    assert list(PROJECT_INSTRUCTIONS) != sorted(PROJECT_INSTRUCTIONS)


def test_the_project_fixture_template_contributes_instructions() -> None:
    """Without it the expected documents never prove a template's prose arrives, and
    the tier ordering between template and project is untested."""
    assert (PROJECT_FIXTURES / "templates" / "web" / "instructions.md").is_file()


def test_every_declared_instruction_fragment_exists() -> None:
    for name in PROJECT_INSTRUCTIONS:
        assert (PROJECT_FIXTURES / "instructions" / f"{name}.md").is_file(), name
