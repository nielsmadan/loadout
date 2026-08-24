from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.permissions.rules import (
    EMPTY_RULES,
    UNSTATED_DEFAULT,
    Rules,
    dedupe,
    is_glob,
    mcp_native,
    mcp_parts,
    parse_rules,
    strictest,
)

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "permissions.toml"


def test_dedupe_preserves_order() -> None:
    assert dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_is_glob_detects_trailing_star() -> None:
    assert is_glob("gamma-*")
    assert not is_glob("beta sub")


def test_mcp_parts_splits_server_and_tool() -> None:
    assert mcp_parts("svc/*") == ("svc", "*")


@pytest.mark.parametrize("bad", ["svc", "/tool", "server/", ""])
def test_mcp_parts_rejects_malformed_targets(bad: str) -> None:
    with pytest.raises(ValueError):
        mcp_parts(bad)


def test_mcp_native_builds_claude_tool_name() -> None:
    assert mcp_native("jina/search") == "mcp__jina__search"


def test_parse_rules_reads_every_section() -> None:
    rules = parse_rules(SOURCE)
    assert "alpha" in rules.allow
    assert "iota push" in rules.deny
    assert "theta check" in rules.ask
    assert rules.mcp_allow == ("svc/*", "svc.two/read")
    assert "Read(//tmp/**)" in rules.claude_extra_allow
    assert rules.opencode_extra["webfetch"] == "allow"


def test_parse_rules_dedupes_within_a_category() -> None:
    rules = parse_rules(SOURCE)
    assert len(rules.allow) == len(set(rules.allow))


def test_shell_and_mcp_accessors() -> None:
    rules = parse_rules(SOURCE)
    assert rules.shell("deny") == rules.deny
    assert rules.mcp("allow") == rules.mcp_allow


def test_empty_rules_has_every_category_empty() -> None:
    assert EMPTY_RULES.allow == ()
    assert EMPTY_RULES.deny == ()
    assert EMPTY_RULES.ask == ()
    assert EMPTY_RULES.mcp_allow == ()
    assert EMPTY_RULES.claude_extra_allow == ()
    assert EMPTY_RULES.opencode_extra == {}


def test_parse_rules_reads_the_catch_all_default(tmp_path: Path) -> None:
    source = tmp_path / "permissions.toml"
    source.write_text('[shell]\ndefault = "allow"\nallow = ["ls"]\n', encoding="utf-8")
    rules = parse_rules(source)
    assert rules.default == "allow"
    assert rules.allow == ("ls",)


def test_a_source_that_states_no_default_leaves_it_unstated(tmp_path: Path) -> None:
    """None, not "ask" — merge_rules must be able to tell the two apart."""
    source = tmp_path / "permissions.toml"
    source.write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    assert parse_rules(source).default is None
    assert parse_rules(source).allow == ("ls",)
    assert EMPTY_RULES.default is None


def test_parse_rules_rejects_a_default_that_is_not_a_decision(tmp_path: Path) -> None:
    bad = tmp_path / "permissions.toml"
    bad.write_text('[shell]\ndefault = "prompt"\n', encoding="utf-8")
    with pytest.raises(LoadoutError, match="allow, ask, deny") as excinfo:
        parse_rules(bad)
    assert str(bad) in str(excinfo.value)


@pytest.mark.parametrize("bad", ["1", "true", '["allow"]'])
def test_parse_rules_rejects_a_default_that_is_not_a_string(tmp_path: Path, bad: str) -> None:
    """`_parse_default` guards the type as well as the value; a TOML integer or
    boolean would otherwise reach `strictest` and raise a bare ValueError."""
    source = tmp_path / "permissions.toml"
    source.write_text(f"[shell]\ndefault = {bad}\n", encoding="utf-8")
    with pytest.raises(LoadoutError, match=r"shell\.default"):
        parse_rules(source)


@pytest.mark.parametrize("category", ["allow", "ask", "deny"])
def test_parse_rules_refuses_a_bare_catch_all_entry(tmp_path: Path, category: str) -> None:
    """Two spellings of one concept resolve by different algebras — strictest-wins
    for the key, last-match-wins for the entry — and on OpenCode the entry lands
    exactly where the seed sits, so no extractor can tell them apart."""
    bad = tmp_path / "permissions.toml"
    bad.write_text(f'[shell]\n{category} = ["ls", "*"]\n', encoding="utf-8")
    with pytest.raises(LoadoutError, match="default") as excinfo:
        parse_rules(bad)
    assert f"shell.{category}" in str(excinfo.value)


def test_a_glob_entry_is_still_allowed_beside_the_refused_bare_star(tmp_path: Path) -> None:
    """The refusal is the bare `*` only — `gamma-*` is a legitimate glob."""
    source = tmp_path / "permissions.toml"
    source.write_text('[shell]\nallow = ["gamma-*"]\n', encoding="utf-8")
    assert parse_rules(source).allow == ("gamma-*",)


def test_catch_all_resolves_unstated_to_the_seeded_verdict() -> None:
    """Renderers read this, so it must never hand back None — that emits `null`."""
    assert Rules().catch_all == UNSTATED_DEFAULT
    assert Rules(default="deny").catch_all == "deny"


def test_strictest_orders_deny_above_ask_above_allow() -> None:
    assert strictest(["allow", "ask"]) == "ask"
    assert strictest(["ask", "deny"]) == "deny"
    assert strictest(["deny", "allow"]) == "deny"
    assert strictest(["allow"]) == "allow"


def test_parse_rules_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError):
        parse_rules(tmp_path / "nope.toml")


def test_parse_rules_rejects_a_malformed_mcp_target(tmp_path: Path) -> None:
    bad = tmp_path / "permissions.toml"
    bad.write_text('[mcp]\nallow = ["noslash"]\n', encoding="utf-8")
    with pytest.raises(LoadoutError, match="noslash") as excinfo:
        parse_rules(bad)
    assert str(bad) in str(excinfo.value)


def test_parse_rules_invalid_toml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "permissions.toml"
    bad.write_text("[shell\n", encoding="utf-8")
    with pytest.raises(LoadoutError):
        parse_rules(bad)


def test_rules_is_frozen() -> None:
    rules = parse_rules(SOURCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rules.allow = ()  # type: ignore[misc]
