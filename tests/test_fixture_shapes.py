"""The fixture's reach, guarded.

The expected-output comparison is only as good as the shapes its source carries. A
trimmed fixture would still pass every comparison while quietly covering less, so each
shape the comparison relies on is asserted here. A failure means the fixture
lost reach — restore the entry rather than deleting the assertion.

Matcher semantics are not tested here; tests/test_permissions_renderers.py owns those.
"""

from __future__ import annotations

import json
from pathlib import Path

from fixture_root import PROJECT_INSTRUCTIONS
from loadout.manifest import MANIFEST_NAME, load_manifest
from loadout.permissions.rules import UNSTATED_DEFAULT, is_exact, is_glob, mcp_parts, parse_rules
from loadout.servers import parse_servers
from loadout.stealth_manifest import load_stealth_manifest

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


def test_an_exact_allow_and_an_exact_deny_are_both_present() -> None:
    """They take opposite paths where a harness has no exact match: Codex and Droid
    omit the allow and widen the deny to its prefix."""
    assert [entry for entry in RULES.allow if is_exact(entry)]
    assert [entry for entry in RULES.deny if is_exact(entry)]


def test_the_project_fixture_carries_an_exact_allow_and_an_exact_deny() -> None:
    """The project Codex renderer is the only path to `codex-project`'s handling."""
    project = parse_rules(PROJECT_FIXTURES / "permissions.toml")
    assert [entry for entry in project.allow if is_exact(entry)]
    assert [entry for entry in project.deny if is_exact(entry)]


def test_a_catch_all_default_is_stated_and_is_not_the_unstated_verdict() -> None:
    """Seeds OpenCode's and Pi's bash catch-all. `ask` would render the same bytes
    as no key at all, so only another verdict makes the shape observable."""
    assert RULES.default is not None
    assert RULES.default != UNSTATED_DEFAULT


def test_an_entry_appears_in_two_categories_and_is_not_last() -> None:
    """Provokes deny-wins resolution end to end — merge_rules must drop the
    allow and keep the deny, and OpenCode's entry must move to the deny
    position while Pi's output is unchanged.

    This guarded ADR 0006's key-move until global scope started merging. It no
    longer can: after resolution no entry is in two categories, so render_pi's
    pop-and-reassign never fires through the pipeline. The quirk is pinned by
    test_pi_reorders_cross_key_entries_so_later_category_wins_position, which
    builds Rules directly.
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
    destinations = [str(d) for target in manifest.targets for d in target.destinations]
    assert [t for t in destinations if "${" in t], "no destination exercises ${...} expansion"


PROJECT_FIXTURES = Path(__file__).parent / "fixtures" / "project"
KIT_RULES = parse_rules(PROJECT_FIXTURES / "kits" / "web" / "permissions.toml")


def test_the_project_fixture_has_a_server_with_a_literal_header_and_an_auth_variable() -> None:
    """Drives the project server renderers' `headers` (Claude, Droid, OpenCode; Pi reads
    `.mcp.json`), with the auth variable's Authorization header ahead of the literal one."""
    servers = parse_servers(PROJECT_FIXTURES / "mcp.toml").values()
    assert [s for s in servers if s.headers and s.auth_env_var]


def test_the_project_tiers_disagree_about_the_catch_all_default() -> None:
    """Project scope must exercise strictest-wins over real tiers, not just units.

    The kit states the loosest verdict, the project tier a stricter one, and
    the personal tier none at all — so the expected output pins all three rules at
    once: the strict one wins, the loose one loses, and silence does not vote.
    """
    kit = parse_rules(PROJECT_FIXTURES / "kits" / "web" / "permissions.toml")
    project = parse_rules(PROJECT_FIXTURES / "permissions.toml")
    personal = parse_rules(PROJECT_FIXTURES / "permissions.local.toml")
    assert kit.default is not None
    assert project.default is not None
    assert kit.default != project.default, "the tiers must disagree or nothing is resolved"
    assert personal.default is None, "a silent tier is what proves silence casts no vote"


def test_the_project_fixture_carries_a_vendored_kit() -> None:
    """Without it, the expected project output never proves a kit reaches it."""
    assert KIT_RULES.allow, "the fixture kit contributes no allow rule"
    assert KIT_RULES.deny, "the fixture kit contributes no deny rule"
    assert KIT_RULES.mcp_allow, "the fixture kit contributes no MCP entry"


def test_every_kit_rule_is_absent_from_the_other_project_tiers() -> None:
    """A rule a project tier already carries would render identically whether or not
    the kit merged, so the comparison would pass with kits switched off."""
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
    kit = set(KIT_RULES.allow + KIT_RULES.deny + KIT_RULES.mcp_allow)
    assert not (kit & others), f"already in another tier: {sorted(kit & others)}"


def test_the_project_fixture_declares_instructions_out_of_sorted_order() -> None:
    """`test_instruction_blocks_appear_in_declared_order_above_the_kit` proves
    nothing if the declared order happens to match the directory listing — a render
    that sorted its fragments would pass. Reversing the pair is what gives that test
    something to catch, so it is a property of the fixture, not of the assertion."""
    assert list(PROJECT_INSTRUCTIONS) != sorted(PROJECT_INSTRUCTIONS)


def test_the_project_fixture_kit_contributes_instructions() -> None:
    """Without it the expected documents never prove a kit's prose arrives, and
    the tier ordering between kit and project is untested."""
    assert (PROJECT_FIXTURES / "kits" / "web" / "instructions.md").is_file()


def test_every_declared_instruction_fragment_exists() -> None:
    for name in PROJECT_INSTRUCTIONS:
        assert (PROJECT_FIXTURES / "instructions" / f"{name}.md").is_file(), name


def test_the_project_plugins_fragment_declares_a_marketplace_and_an_addressable_plugin() -> None:
    """The project-scope plugins fragment is what proves a `ValuesSpec` composes
    into a document another slice co-owns. Trimmed to `{}` it renders two empty
    maps, every comparison still passes, and both the composition and the
    used-marketplace filter go untested."""
    document = json.loads((PROJECT_FIXTURES / "plugins.json").read_text(encoding="utf-8"))
    assert document["marketplaces"], "no marketplace to declare"
    plugins = document["plugins"]
    assert plugins, "no plugin to enable"
    assert [
        entry for entry in plugins.values() if entry.get("marketplace") in document["marketplaces"]
    ], "no plugin reaches a declared marketplace, so nothing is addressable"


def test_the_project_plugins_fragment_switches_an_addressable_plugin_off() -> None:
    """Droid renders a switched-off plugin as `false`; without one in the fixture the
    expected settings never show it."""
    document = json.loads((PROJECT_FIXTURES / "plugins.json").read_text(encoding="utf-8"))
    assert [
        name
        for name, entry in document["plugins"].items()
        if entry.get("enabled") is False and entry.get("marketplace") in document["marketplaces"]
    ], "no addressable plugin is switched off"


def test_the_project_hooks_fragment_declares_an_event_with_a_command() -> None:
    """`.factory/hooks.json` is generated from this alone. An empty document
    renders an empty file that matches its expected copy exactly."""
    document = json.loads((PROJECT_FIXTURES / "hooks.json").read_text(encoding="utf-8"))
    assert document, "no event to render"
    assert [
        hook
        for entries in document.values()
        for entry in entries
        for hook in entry.get("hooks", ())
        if hook.get("command")
    ], "no event carries a command"


GLOBAL_SERVER_RENDERERS = {"claude-servers", "codex-servers", "codex-config", "pi-servers"}


def test_the_global_fixture_defines_an_http_server_with_a_header_and_a_stdio_server() -> None:
    """Pins Pi's `headers` and Codex's `http_headers`, which no project destination
    renders, beside the stdio shape on the same renderers."""
    servers = parse_servers(FIXTURE.parent / "mcp.toml").values()
    assert [s for s in servers if s.transport == "http" and s.headers and s.auth_env_var]
    assert [s for s in servers if s.transport == "stdio" and s.env]


def test_the_global_fixture_routes_its_servers_through_each_global_renderer() -> None:
    manifest = load_manifest(FIXTURE.parent / MANIFEST_NAME)
    assert {target.renderer for target in manifest.permissions} >= GLOBAL_SERVER_RENDERERS


STEALTH_FIXTURE = Path(__file__).parent / "fixtures" / "stealth"


def test_the_stealth_fixture_declares_all_five_harnesses_and_one_differing_fragment_order() -> None:
    manifest = load_stealth_manifest(STEALTH_FIXTURE)
    assert manifest.harnesses == ("claude", "codex", "droid", "opencode", "pi")
    orders = {t.name: t.fragments for t in manifest.manifest.targets}
    assert len(set(orders.values())) == 2


def test_the_stealth_fixture_has_every_permission_category_and_no_stated_default() -> None:
    rules = parse_rules(STEALTH_FIXTURE / "permissions.toml")
    assert all(rules.shell(category) for category in ("allow", "ask", "deny"))
    assert len(rules.mcp_allow + rules.mcp_ask + rules.mcp_deny) == 1
    assert rules.default is None


def test_the_stealth_fixture_has_an_http_and_a_stdio_server_and_a_settings_fragment() -> None:
    servers = parse_servers(STEALTH_FIXTURE / "mcp.toml").values()
    assert {s.transport for s in servers} == {"http", "stdio"}
    settings = json.loads((STEALTH_FIXTURE / "settings/local.json").read_text(encoding="utf-8"))
    assert settings["model"] and settings["permissions"]["defaultMode"]


def test_the_stealth_fixture_skill_has_a_supporting_file() -> None:
    assert (STEALTH_FIXTURE / "skills/probe/SKILL.md").is_file()
    assert (STEALTH_FIXTURE / "skills/probe/reference.md").is_file()
