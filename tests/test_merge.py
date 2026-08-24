from __future__ import annotations

import pytest

from loadout.permissions.merge import merge_rules
from loadout.permissions.rules import EMPTY_RULES, Rules


def test_merging_one_tier_with_empty_is_the_identity() -> None:
    """Required for the byte-identical port: one populated tier must pass through."""
    rules = Rules(
        allow=("git status", "ls"),
        deny=("git push",),
        ask=("heroku",),
        mcp_allow=("jina/*",),
        claude_extra_allow=("WebSearch",),
        opencode_extra={"webfetch": "allow"},
    )
    assert merge_rules(rules, EMPTY_RULES) == rules
    assert merge_rules(EMPTY_RULES, rules) == rules


@pytest.mark.parametrize(
    ("stricter", "weaker"),
    [("ask", "allow"), ("deny", "ask"), ("deny", "allow")],
    ids=["ask-beats-allow", "deny-beats-ask", "deny-beats-allow"],
)
def test_the_strictest_stated_default_wins_regardless_of_tier_order(
    stricter: str, weaker: str
) -> None:
    """Every adjacent pair, so the strictness ORDER is pinned and not just the fact
    that a tier arrived. Reordering `DECISIONS` makes deny-beats-ask fail — without
    that case the ordering is unpinned, since nothing else in the suite reads it.
    """
    strict = Rules(allow=("ls",), default=stricter)
    loose = Rules(allow=("pwd",), default=weaker)
    for tiers in ((strict, loose), (loose, strict)):
        merged = merge_rules(*tiers)
        assert merged.default == stricter
        # The loser was there and lost: its rule survives the merge either way.
        assert set(merged.allow) == {"ls", "pwd"}


def test_a_tier_that_states_no_default_does_not_tighten_one_that_does() -> None:
    """An absent key is not a tier voting `ask`. Without this a project source
    that never mentions the key would silently narrow the machine's default."""
    stated = Rules(allow=("ls",), default="allow")
    silent = Rules(allow=("pwd",))
    assert merge_rules(stated, silent).default == "allow"
    assert merge_rules(silent, stated).default == "allow"
    assert merge_rules(silent, silent).default is None
    # The silent tier was present in both orders, not skipped.
    assert merge_rules(stated, silent).allow == ("ls", "pwd")
    assert merge_rules(silent, stated).allow == ("pwd", "ls")


def test_union_of_disjoint_tiers_preserves_order_within_each() -> None:
    a = Rules(allow=("one", "two"))
    b = Rules(allow=("three",))
    assert merge_rules(a, b).allow == ("one", "two", "three")


def test_duplicate_across_tiers_appears_once() -> None:
    a = Rules(allow=("ls", "pwd"))
    b = Rules(allow=("pwd", "cat"))
    assert merge_rules(a, b).allow == ("ls", "pwd", "cat")


def test_deny_beats_allow_regardless_of_tier_order() -> None:
    """The decision is order-independent here only because deny drops the
    other entry entirely, so there is no differing position left to compare.
    This is not a general commutativity test — see
    test_three_way_conflict_resolves_to_deny_under_any_permutation for that.
    """
    a = Rules(allow=("git push",))
    b = Rules(deny=("git push",))
    assert merge_rules(a, b).deny == ("git push",)
    assert merge_rules(a, b).allow == ()
    assert merge_rules(b, a) == merge_rules(a, b)


def test_three_way_conflict_resolves_to_deny_under_any_permutation() -> None:
    """The security-relevant property: whichever tier denies an entry, that
    entry ends up denied, no matter which tier contributed the deny or where
    it sits in argument order. Emission order and opencode_extra conflicts
    are NOT order-independent (see merge_rules's docstring and ADR 0002) —
    only the decision is.
    """
    denies = Rules(deny=("heroku",))
    asks = Rules(ask=("heroku",))
    allows = Rules(allow=("heroku",))

    merged = merge_rules(denies, asks, allows)
    assert merged.deny == ("heroku",)
    assert merged.ask == ()
    assert merged.allow == ()

    merged_reordered = merge_rules(allows, denies, asks)
    assert merged_reordered.deny == ("heroku",)
    assert merged_reordered.ask == ()
    assert merged_reordered.allow == ()


def test_mcp_categories_preserve_order_across_multiple_tiers() -> None:
    a = Rules(mcp_allow=("jina/read", "jina/search"))
    b = Rules(mcp_allow=("github/pr",))
    c = Rules(mcp_allow=("jina/search", "linear/issue"))
    assert merge_rules(a, b, c).mcp_allow == (
        "jina/read",
        "jina/search",
        "github/pr",
        "linear/issue",
    )


def test_deny_beats_ask() -> None:
    merged = merge_rules(Rules(ask=("heroku",)), Rules(deny=("heroku",)))
    assert merged.deny == ("heroku",)
    assert merged.ask == ()


def test_ask_beats_allow() -> None:
    merged = merge_rules(Rules(allow=("vercel",)), Rules(ask=("vercel",)))
    assert merged.ask == ("vercel",)
    assert merged.allow == ()


def test_mcp_categories_resolve_the_same_way() -> None:
    merged = merge_rules(Rules(mcp_allow=("jina/*",)), Rules(mcp_deny=("jina/*",)))
    assert merged.mcp_deny == ("jina/*",)
    assert merged.mcp_allow == ()


def test_claude_extras_union_without_precedence() -> None:
    merged = merge_rules(
        Rules(claude_extra_allow=("WebSearch",)),
        Rules(claude_extra_allow=("WebFetch",)),
    )
    assert merged.claude_extra_allow == ("WebSearch", "WebFetch")


def test_opencode_extra_later_tier_wins_on_the_same_key() -> None:
    merged = merge_rules(
        Rules(opencode_extra={"webfetch": "allow", "skill": "allow"}),
        Rules(opencode_extra={"webfetch": "ask"}),
    )
    assert merged.opencode_extra == {"webfetch": "ask", "skill": "allow"}


def test_merging_nothing_yields_empty_rules() -> None:
    assert merge_rules() == EMPTY_RULES


def test_associative_across_three_tiers() -> None:
    a, b, c = Rules(allow=("a",)), Rules(allow=("b",)), Rules(deny=("a",))
    assert merge_rules(merge_rules(a, b), c) == merge_rules(a, merge_rules(b, c))
