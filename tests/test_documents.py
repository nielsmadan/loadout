from __future__ import annotations

from typing import Any

from loadout.documents import merge_documents


def test_no_documents_is_an_empty_document() -> None:
    assert merge_documents() == {}


def test_one_document_is_copied_not_returned() -> None:
    source: dict[str, Any] = {"a": 1}
    merged = merge_documents(source)
    assert merged == source
    assert merged is not source


def test_maps_merge_recursively() -> None:
    merged = merge_documents({"env": {"A": "1"}}, {"env": {"B": "2"}})
    assert merged == {"env": {"A": "1", "B": "2"}}


def test_a_later_scalar_wins() -> None:
    assert merge_documents({"model": "opus"}, {"model": "sonnet"}) == {"model": "sonnet"}


def test_key_position_comes_from_first_appearance() -> None:
    """Key order is load-bearing; adding to a key must not move it."""
    merged = merge_documents({"a": 1, "b": 2, "c": 3}, {"b": 20})
    assert list(merged) == ["a", "b", "c"]


def test_a_new_key_appends_in_document_order() -> None:
    merged = merge_documents({"a": 1}, {"c": 3}, {"b": 2})
    assert list(merged) == ["a", "c", "b"]


def test_nested_key_position_also_comes_from_first_appearance() -> None:
    merged = merge_documents({"env": {"A": "1", "B": "2"}}, {"env": {"A": "9"}})
    assert list(merged["env"]) == ["A", "B"]


def test_lists_concatenate_in_argument_order() -> None:
    merged = merge_documents({"hooks": ["first"]}, {"hooks": ["second"]})
    assert merged["hooks"] == ["first", "second"]


def test_lists_are_not_deduplicated() -> None:
    """Two fragments naming the same command can be deliberate."""
    merged = merge_documents({"hooks": ["same"]}, {"hooks": ["same"]})
    assert merged["hooks"] == ["same", "same"]


def test_a_hook_event_gathers_entries_from_every_fragment() -> None:
    """The case the operator exists for — two fragments adding to one event."""
    merged = merge_documents(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"command": "a"}]}]}},
        {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"command": "b"}]}]}},
    )
    assert [e["matcher"] for e in merged["hooks"]["PreToolUse"]] == ["Bash", "*"]


def test_none_removes_a_key() -> None:
    assert merge_documents({"a": 1, "b": 2}, {"a": None}) == {"b": 2}


def test_none_removes_a_nested_key() -> None:
    merged = merge_documents({"env": {"A": "1", "B": "2"}}, {"env": {"A": None}})
    assert merged == {"env": {"B": "2"}}


def test_none_removes_a_list_wholesale() -> None:
    """There is no syntax for removing one element; the whole key goes."""
    merged = merge_documents({"hooks": ["a", "b"]}, {"hooks": None})
    assert merged == {}


def test_removing_an_absent_key_is_not_an_error() -> None:
    assert merge_documents({"a": 1}, {"b": None}) == {"a": 1}


def test_removal_is_total_so_a_reintroduced_key_lands_at_the_end() -> None:
    """Removal drops the position too, rather than leaving a tombstone.

    "Position from first appearance" applies to keys that are present. A key
    that `None` removed is genuinely gone, so a later fragment adding it back is
    a first appearance. The alternative — remembering where a deleted key used
    to sit — is spooky and needs tombstones for a case nothing has yet wanted.
    """
    merged = merge_documents({"a": 1, "b": 2}, {"a": None}, {"a": 3})
    assert merged == {"a": 3, "b": 2}
    assert list(merged) == ["b", "a"]


def test_a_map_arriving_over_a_scalar_replaces_it() -> None:
    assert merge_documents({"a": 1}, {"a": {"b": 2}}) == {"a": {"b": 2}}


def test_a_scalar_arriving_over_a_map_replaces_it() -> None:
    assert merge_documents({"a": {"b": 2}}, {"a": 1}) == {"a": 1}


def test_a_list_arriving_over_a_scalar_replaces_it() -> None:
    assert merge_documents({"a": 1}, {"a": [2]}) == {"a": [2]}


def test_a_nested_none_inside_a_replacing_map_is_not_copied_through() -> None:
    """A literal null reaching a generated file would be a rendered removal."""
    merged = merge_documents({"a": 1}, {"a": {"b": 2, "c": None}})
    assert merged == {"a": {"b": 2}}


def test_arguments_are_not_mutated() -> None:
    first: dict[str, Any] = {"env": {"A": "1"}, "hooks": ["a"]}
    second: dict[str, Any] = {"env": {"B": "2"}, "hooks": ["b"]}
    merge_documents(first, second)
    assert first == {"env": {"A": "1"}, "hooks": ["a"]}
    assert second == {"env": {"B": "2"}, "hooks": ["b"]}


def test_nested_values_are_not_shared_with_the_arguments() -> None:
    source: dict[str, Any] = {"env": {"A": "1"}, "hooks": ["a"]}
    merged = merge_documents(source)
    merged["env"]["A"] = "changed"
    merged["hooks"].append("added")
    assert source == {"env": {"A": "1"}, "hooks": ["a"]}


def test_merging_is_associative_over_three_documents() -> None:
    a: dict[str, Any] = {"x": {"p": 1}, "l": [1]}
    b: dict[str, Any] = {"x": {"q": 2}, "l": [2]}
    c: dict[str, Any] = {"x": {"p": 9}, "l": [3]}
    assert merge_documents(merge_documents(a, b), c) == merge_documents(a, merge_documents(b, c))
    assert merge_documents(a, b, c) == {"x": {"p": 9, "q": 2}, "l": [1, 2, 3]}
