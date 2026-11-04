from __future__ import annotations

import json

import pytest

from loadout.errors import LoadoutError
from loadout.json_paths import (
    decode_path,
    encode_path,
    leaf_digest,
    lookup,
    with_path,
    without_path,
)


def test_with_path_sets_a_segment_holding_slashes_and_dots_without_touching_the_input() -> None:
    document = {"projects": {"/a/b.c": {"other": 1}}, "tail": True}
    snapshot = json.dumps(document)
    result = with_path(document, ("projects", "/a/b.c", "mcpServers", "x.y"), {"command": "z"})
    assert json.dumps(result) == json.dumps(
        {
            "projects": {"/a/b.c": {"other": 1, "mcpServers": {"x.y": {"command": "z"}}}},
            "tail": True,
        }
    )
    assert json.dumps(document) == snapshot


def test_with_path_keeps_an_existing_key_in_place_and_appends_a_new_one() -> None:
    document = {"a": 1, "b": 2, "c": 3}
    assert list(with_path(document, ("b",), 9).items()) == [("a", 1), ("b", 9), ("c", 3)]
    assert list(with_path(document, ("d",), 4)) == ["a", "b", "c", "d"]


def test_with_path_refuses_to_walk_through_a_non_object() -> None:
    with pytest.raises(LoadoutError, match=r"a\.b"):
        with_path({"a": [1]}, ("a", "b"), 1)


def test_without_path_removes_only_the_leaf() -> None:
    document = {"projects": {"/r": {"mcpServers": {"x": {}}, "keep": 1}}}
    assert without_path(document, ("projects", "/r", "mcpServers", "x")) == {
        "projects": {"/r": {"mcpServers": {}, "keep": 1}}
    }
    assert document == {"projects": {"/r": {"mcpServers": {"x": {}}, "keep": 1}}}


def test_lookup_reports_presence_separately_from_value() -> None:
    assert lookup({"a": {"b": None}}, ("a", "b")) == (True, None)
    assert lookup({"a": {"b": None}}, ("a", "c")) == (False, None)


def test_leaf_digest_tells_true_from_one() -> None:
    assert leaf_digest(True) != leaf_digest(1)
    assert leaf_digest({"a": 1, "b": 2}) == leaf_digest({"b": 2, "a": 1})


def test_paths_round_trip_and_bad_shapes_decode_to_none() -> None:
    path = ("projects", "/a.b/c", "mcpServers", "x")
    assert decode_path(encode_path(path)) == path
    assert [decode_path(raw) for raw in ([], ["a", 1], "a.b", None)] == [None] * 4
