from __future__ import annotations

import json

import pytest

from loadout.claude_json import judge_leaves
from loadout.errors import LoadoutError
from loadout.json_paths import (
    decode_path,
    display_path,
    encode_path,
    leaf_digest,
    lookup,
    with_path,
    without_path,
)
from loadout.stealth_documents import DocumentRecord, judge_strip


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


def test_leaves_and_documents_judge_a_retiring_key_the_same_way() -> None:
    written, mine, edited = {"command": "loadout"}, {"command": "mine"}, {"command": "edited"}
    document = {"pending_match": written, "pending_other": mine, "owned_edited": edited}
    pending = {("pending_match",): leaf_digest(written), ("pending_other",): leaf_digest(written)}
    owned = {("owned_edited",): leaf_digest(written)}
    leaves = judge_leaves(
        document,
        {},
        {path: frozenset({digest}) for path, digest in owned.items()},
        (*pending, *owned),
        pending=pending,
    )
    documents = judge_strip(
        document,
        DocumentRecord(created=False, owned_keys=owned, owned_rules={}, pending_keys=pending),
    )
    expected = [(("pending_match",), "retire"), (("owned_edited",), "owned-key")]
    assert sorted((verdict.path, verdict.kind) for verdict in leaves) == sorted(expected)
    assert sorted((verdict.path, verdict.kind) for verdict in documents) == sorted(expected)
    assert len(document) == len(expected) + 1


def test_display_path_quotes_a_segment_that_holds_a_dot_or_a_slash() -> None:
    assert display_path(("permissions", "allow")) == "permissions.allow"
    assert (
        display_path(("projects", "/a/b.c", "mcpServers", "x.y", "local-tool"))
        == 'projects["/a/b.c"].mcpServers["x.y"].local-tool'
    )
