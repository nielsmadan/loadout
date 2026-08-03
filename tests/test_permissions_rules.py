from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.permissions.rules import (
    EMPTY_RULES,
    dedupe,
    is_glob,
    mcp_native,
    mcp_parts,
    parse_rules,
)

GOLDEN = Path(__file__).parent / "golden"
SOURCE = GOLDEN / "permissions" / "permissions.toml"


def test_dedupe_preserves_order() -> None:
    assert dedupe(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]


def test_is_glob_detects_trailing_star() -> None:
    assert is_glob("docker exec cc-workbench-*")
    assert not is_glob("git status")


def test_mcp_parts_splits_server_and_tool() -> None:
    assert mcp_parts("jina/*") == ("jina", "*")


@pytest.mark.parametrize("bad", ["jina", "/tool", "server/", ""])
def test_mcp_parts_rejects_malformed_targets(bad: str) -> None:
    with pytest.raises(ValueError):
        mcp_parts(bad)


def test_mcp_native_builds_claude_tool_name() -> None:
    assert mcp_native("jina/search") == "mcp__jina__search"


def test_parse_rules_reads_every_section() -> None:
    rules = parse_rules(SOURCE)
    assert "git status" in rules.allow
    assert "git push" in rules.deny
    assert "heroku" in rules.ask
    assert rules.mcp_allow == ("jina/*",)
    assert "WebSearch" in rules.claude_extra_allow
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


def test_parse_rules_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError):
        parse_rules(tmp_path / "nope.toml")


def test_parse_rules_invalid_toml_raises(tmp_path: Path) -> None:
    bad = tmp_path / "permissions.toml"
    bad.write_text("[shell\n", encoding="utf-8")
    with pytest.raises(LoadoutError):
        parse_rules(bad)


def test_rules_is_frozen() -> None:
    rules = parse_rules(SOURCE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rules.allow = ()  # type: ignore[misc]
