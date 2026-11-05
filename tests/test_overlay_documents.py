from __future__ import annotations

import json
from typing import Any

from loadout.json_paths import leaf_digest
from loadout.overlay_documents import (
    DocumentRecord,
    DocumentVerdict,
    OwnedJson,
    apply_owned,
    carry_saved_rules,
    judge_document,
    judge_strip,
    strip_owned,
)

ALLOW = ("permissions", "allow")
DENY = ("permissions", "deny")


def dumped(document: object) -> str:
    return json.dumps(document, indent=2)


def claude_owned(model: str = "opus") -> OwnedJson:
    return OwnedJson(
        keys=((("model",), model),),
        rules=((ALLOW, ("Bash(ls)", "Read")), (DENY, ("Bash(rm -rf /)",))),
    )


def opencode_owned(*, mcp: bool = True) -> OwnedJson:
    keys: list[tuple[tuple[str, ...], Any]] = [(("permission",), {"bash": {"ls": "allow"}})]
    if mcp:
        keys.append((("mcp",), {"docs": {"type": "local", "command": ["docs"]}}))
    keys.append((("instructions",), ["/overlay/.loadout/opencode-instructions.md"]))
    return OwnedJson(keys=tuple(keys), rules=())


def test_carry_saved_rules_keeps_saved_approvals_and_drops_what_the_overlay_wrote() -> None:
    assert carry_saved_rules(["A"], ["X", "A", "B"], ["B"]) == (["A", "X"], ("X",))


def test_claude_keys_and_saved_approvals_survive_an_owned_write() -> None:
    existing = {
        "outputStyle": "Explanatory",
        "skillOverrides": {"pdf": {"enabled": False}},
        "permissions": {"allow": ["Bash(npm run x)"]},
    }
    applied = apply_owned(existing, claude_owned(), None)
    assert dumped(applied.document) == dumped(
        {
            "outputStyle": "Explanatory",
            "skillOverrides": {"pdf": {"enabled": False}},
            "permissions": {
                "allow": ["Bash(ls)", "Read", "Bash(npm run x)"],
                "deny": ["Bash(rm -rf /)"],
            },
            "model": "opus",
        }
    )
    assert applied.carried == ("permissions.allow: Bash(npm run x)",)
    assert applied.changed is True


def test_dropping_a_slice_removes_its_key_and_a_foreign_key_keeps_its_position() -> None:
    first = apply_owned({"theme": "dark"}, opencode_owned(), None)
    edited = {**first.document, "share": "manual"}
    second = apply_owned(edited, opencode_owned(mcp=False), first.record)
    assert list(second.document) == ["theme", "permission", "instructions", "share"]
    assert second.document["theme"] == "dark"
    assert second.document["share"] == "manual"
    assert second.changed is True
    assert list(second.record.owned_keys) == [("permission",), ("instructions",)]


def test_a_pre_existing_file_is_recorded_as_not_created() -> None:
    assert apply_owned({"theme": "dark"}, opencode_owned(), None).record.created is False
    assert apply_owned(None, opencode_owned(), None).record.created is True


def test_carried_entries_before_owned_ones_need_no_write() -> None:
    first = apply_owned(None, claude_owned(), None)
    reordered = {
        "model": "opus",
        "permissions": {
            "deny": ["Bash(rm -rf /)"],
            "allow": ["Bash(npm run x)", "Read", "Bash(ls)"],
        },
    }
    again = apply_owned(reordered, claude_owned(), first.record)
    assert again.changed is False
    assert again.carried == ("permissions.allow: Bash(npm run x)",)


def test_an_unchanged_document_needs_no_write() -> None:
    first = apply_owned(None, claude_owned(), None)
    again = apply_owned(first.document, claude_owned(), first.record)
    assert again.changed is False
    assert again.document == first.document


def test_a_list_with_nothing_to_hold_is_not_created() -> None:
    owned = OwnedJson(keys=(), rules=((ALLOW, ("Read",)), (("permissions", "ask"), ())))
    assert apply_owned({}, owned, None).document == {"permissions": {"allow": ["Read"]}}


def test_an_entry_the_overlay_stops_rendering_is_removed() -> None:
    first = apply_owned(None, claude_owned(), None)
    owned = OwnedJson(keys=((("model",), "opus"),), rules=((ALLOW, ("Read",)),))
    again = apply_owned(first.document, owned, first.record)
    assert again.document == {"model": "opus", "permissions": {"allow": ["Read"], "deny": []}}
    assert again.changed is True


def test_an_unrecorded_present_key_is_user_defined_unless_it_holds_the_wanted_value() -> None:
    owned = OwnedJson(keys=((("model",), "opus"),), rules=())
    assert judge_document({"model": "sonnet"}, owned, None) == (
        DocumentVerdict(("model",), "user-defined"),
    )
    assert judge_document({"model": "opus"}, owned, None) == (DocumentVerdict(("model",), "adopt"),)


def test_recorded_keys_are_judged_against_the_record() -> None:
    record = DocumentRecord(
        created=False, owned_keys={("model",): leaf_digest("opus")}, owned_rules={}
    )
    wanted = OwnedJson(keys=((("model",), "sonnet"),), rules=())
    assert judge_document({"model": "haiku"}, wanted, record) == (
        DocumentVerdict(("model",), "owned-key"),
    )
    assert judge_document({"model": "opus"}, wanted, record) == (
        DocumentVerdict(("model",), "update"),
    )
    assert judge_document({}, wanted, record) == (DocumentVerdict(("model",), "owned-key-removed"),)
    assert judge_document({"model": "haiku"}, OwnedJson(keys=(), rules=()), record) == (
        DocumentVerdict(("model",), "owned-key"),
    )
    assert judge_document({"model": "opus"}, OwnedJson(keys=(), rules=()), record) == (
        DocumentVerdict(("model",), "retire"),
    )


def test_a_deleted_owned_entry_is_owned_key_removed() -> None:
    first = apply_owned(None, claude_owned(), None)
    deleted = {"model": "opus", "permissions": {"allow": ["Read"], "deny": ["Bash(rm -rf /)"]}}
    assert judge_document(deleted, claude_owned(), first.record) == (
        DocumentVerdict(("model",), "unchanged"),
        DocumentVerdict(ALLOW, "owned-key-removed"),
        DocumentVerdict(DENY, "unchanged"),
    )


def test_strip_of_a_created_file_holding_only_owned_parts_unlinks_it() -> None:
    first = apply_owned(None, claude_owned(), None)
    assert strip_owned(first.document, first.record) is None


def test_strip_keeps_saved_approvals_and_claude_keys() -> None:
    first = apply_owned(None, claude_owned(), None)
    on_disk = {
        "model": "opus",
        "permissions": {
            "allow": ["Bash(ls)", "Read", "Bash(npm run x)"],
            "deny": ["Bash(rm -rf /)"],
        },
        "outputStyle": "Explanatory",
    }
    assert judge_strip(on_disk, first.record) == (
        DocumentVerdict(("model",), "retire"),
        DocumentVerdict(ALLOW, "retire"),
        DocumentVerdict(DENY, "retire"),
    )
    assert dumped(strip_owned(on_disk, first.record)) == dumped(
        {"permissions": {"allow": ["Bash(npm run x)"]}, "outputStyle": "Explanatory"}
    )


def test_strip_of_a_file_the_overlay_did_not_create_never_unlinks() -> None:
    first = apply_owned({}, claude_owned(), None)
    assert first.record.created is False
    assert strip_owned(first.document, first.record) == {}


def test_a_pre_existing_approval_the_overlay_also_renders_survives_strip() -> None:
    existing = {"permissions": {"allow": ["Read", "Bash(x)"]}}
    owned = OwnedJson(keys=(), rules=((ALLOW, ("Read", "Bash(ls)")),))
    applied = apply_owned(existing, owned, None)
    assert applied.record.owned_rules == {ALLOW: ("Bash(ls)",)}
    assert strip_owned(applied.document, applied.record) == {
        "permissions": {"allow": ["Read", "Bash(x)"]}
    }


def test_an_entry_listed_in_the_record_is_still_owned_when_on_disk() -> None:
    record = DocumentRecord(created=False, owned_keys={}, owned_rules={ALLOW: ("Read",)})
    owned = OwnedJson(keys=(), rules=((ALLOW, ("Read",)),))
    applied = apply_owned({"permissions": {"allow": ["Read"]}}, owned, record)
    assert applied.record.owned_rules == {ALLOW: ("Read",)}


def test_a_list_the_overlay_empties_is_pruned_by_strip_and_the_created_file_unlinks() -> None:
    first = apply_owned(None, claude_owned(), None)
    owned = OwnedJson(keys=((("model",), "opus"),), rules=((ALLOW, ("Read",)),))
    second = apply_owned(first.document, owned, first.record)
    assert second.record.owned_rules == {ALLOW: ("Read",), DENY: ()}
    assert strip_owned(second.document, second.record) is None


def test_a_list_the_overlay_empties_in_a_file_it_did_not_create_is_pruned() -> None:
    owned = OwnedJson(keys=(), rules=((DENY, ("Bash(rm -rf /)",)),))
    first = apply_owned({"outputStyle": "x"}, owned, None)
    emptied = apply_owned(first.document, OwnedJson(keys=(), rules=()), first.record)
    assert emptied.document == {"outputStyle": "x", "permissions": {"deny": []}}
    assert strip_owned(emptied.document, emptied.record) == {"outputStyle": "x"}


def test_a_list_path_the_overlay_renders_nothing_for_gets_no_verdict() -> None:
    existing = {"permissions": {"allow": "everything"}}
    owned = OwnedJson(keys=(), rules=((ALLOW, ()), (DENY, ("Bash(rm -rf /)",))))
    assert judge_document(existing, owned, None) == (DocumentVerdict(DENY, "create"),)
    applied = apply_owned(existing, owned, None)
    assert applied.document == {"permissions": {"allow": "everything", "deny": ["Bash(rm -rf /)"]}}
    assert applied.record.owned_rules == {DENY: ("Bash(rm -rf /)",)}


def test_pending_keys_and_rules_are_created_when_absent_and_adopted_when_written() -> None:
    record = DocumentRecord(
        created=False,
        owned_keys={},
        owned_rules={ALLOW: ("Bash(ls)",)},
        pending_keys={("model",): leaf_digest("opus")},
        pending_rules={ALLOW: ("Read",), DENY: ("Bash(rm -rf /)",)},
    )
    before_the_write = {"permissions": {"allow": ["Bash(ls)", "Bash(npm test)"]}}
    assert judge_document(before_the_write, claude_owned(), record) == (
        DocumentVerdict(("model",), "create"),
        DocumentVerdict(ALLOW, "update"),
        DocumentVerdict(DENY, "create"),
    )

    after_the_write = apply_owned(before_the_write, claude_owned(), record).document
    edited_source = claude_owned("sonnet")
    assert judge_document(after_the_write, edited_source, record) == (
        DocumentVerdict(("model",), "update"),
        DocumentVerdict(ALLOW, "adopt"),
        DocumentVerdict(DENY, "adopt"),
    )
    adopted = apply_owned(after_the_write, edited_source, record)
    assert adopted.record == DocumentRecord(
        created=False,
        owned_keys={("model",): leaf_digest("sonnet")},
        owned_rules={ALLOW: ("Bash(ls)", "Read"), DENY: ("Bash(rm -rf /)",)},
    )
    assert adopted.carried == ("permissions.allow: Bash(npm test)",)


def test_a_pending_key_is_stripped_only_while_it_holds_the_pending_value() -> None:
    record = DocumentRecord(
        created=False,
        owned_keys={},
        owned_rules={},
        pending_keys={("model",): leaf_digest("opus"), ("theme",): leaf_digest("dark")},
        pending_rules={ALLOW: ("Read",)},
    )
    existing = {"model": "opus", "theme": "light", "permissions": {"allow": ["Read", "Mine"]}}
    assert strip_owned(existing, record) == {"theme": "light", "permissions": {"allow": ["Mine"]}}
    assert judge_strip(existing, record) == (
        DocumentVerdict(("model",), "retire"),
        DocumentVerdict(ALLOW, "retire"),
    )


def test_a_rule_that_holds_a_secret_is_carried_without_its_text() -> None:
    token = "ghp_" + "a1B2c3D4" * 4 + "e5F6"
    existing = {"permissions": {"allow": [f"Bash(curl -H 'Authorization: Bearer {token}':*)"]}}
    applied = apply_owned(existing, claude_owned(), None)
    withheld = "a rule not shown because it holds a secret (a token in a provider's format)"
    assert applied.carried == (f"permissions.allow: {withheld}",)
    assert applied.document["permissions"]["allow"][-1].endswith(f"{token}':*)")
