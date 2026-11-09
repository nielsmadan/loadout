"""The inverse of the hook renderers: Claude's and Codex's `hooks` value, and Droid's
standalone `hooks.json`, which is the event map itself.

Two properties, per `docs/reference/extraction.md`:

1. `extract(render(x)) == carried(x)`. For hooks `carried` is **identity** —
   both renderers deep-copy and translate nothing, because the ABI is Claude's
   own hook protocol. Declaring a lossy projection that loses nothing would look
   like the property was doing work it is not. If this ever stops being identity,
   that is a finding about a renderer.
2. `render(extract(doc)) == doc` byte-for-byte, wherever `notes` is empty — and
   `notes == ()` holds **iff** the round trip closes.
"""

from __future__ import annotations

from typing import Any

import pytest

from loadout.errors import LoadoutError
from loadout.extract import EXTRACTORS, VALUE_EXTRACTORS, extract_value
from loadout.hooks import render_claude_hooks, render_codex_hooks, render_droid_hooks
from loadout.permissions.renderers import (
    RENDERERS,
    DocumentJsonSpec,
    MergedJsonSpec,
    ValueSpec,
    ValuesSpec,
)
from test_extract_roundtrip import NOT_INVERTED


def cmd(command: str = "notify.sh") -> dict[str, Any]:
    return {"type": "command", "command": command, "timeout": 5}


FRAGMENT: dict[str, Any] = {
    "PreToolUse": [{"matcher": "Bash", "hooks": [cmd("guard.sh")]}],
    "Stop": [{"hooks": [cmd()]}],
}


def claude_file(fragment: dict[str, Any]) -> dict[str, Any]:
    """What the composing loop writes: the residual, then each owned key."""
    return {"model": "opus", "hooks": render_claude_hooks(fragment), "permissions": {"allow": []}}


def codex_file(fragment: dict[str, Any]) -> dict[str, Any]:
    return {"hooks": render_codex_hooks(fragment)}


def droid_file(fragment: dict[str, Any]) -> dict[str, Any]:
    return render_droid_hooks(fragment)


# --- property 1: extract(render(x)) == carried(x), carried == identity --------


def test_claude_round_trips_the_fragment_unchanged() -> None:
    assert extract_value("claude-hooks", claude_file(FRAGMENT)).value == FRAGMENT


def test_codex_round_trips_the_fragment_unchanged() -> None:
    assert extract_value("codex-hooks", codex_file(FRAGMENT)).value == FRAGMENT


def test_droid_round_trips_the_fragment_unchanged() -> None:
    assert extract_value("droid-hooks", droid_file(FRAGMENT)).value == FRAGMENT


def test_extraction_does_not_alias_the_document() -> None:
    document = claude_file(FRAGMENT)
    extracted = extract_value("claude-hooks", document).value
    extracted["Stop"].append({"hooks": [cmd("other.sh")]})
    assert len(document["hooks"]["Stop"]) == 1


# --- property 2: notes == () iff the round trip closes -----------------------


def test_a_clean_document_reports_nothing_and_closes() -> None:
    for name, build, render in (
        ("claude-hooks", claude_file, render_claude_hooks),
        ("codex-hooks", codex_file, render_codex_hooks),
        ("droid-hooks", droid_file, render_droid_hooks),
    ):
        extraction = extract_value(name, build(FRAGMENT))
        assert extraction.notes == ()
        assert build(extraction.value) == build(FRAGMENT)
        assert render(extraction.value) == FRAGMENT


def test_a_foreign_variable_extracts_and_is_noted() -> None:
    """Extraction must not apply the cross-harness check — refusing would lose a
    hook that exists. But rendering back refuses, so the trip does not close."""
    fragment = {"PreToolUse": [{"hooks": [cmd('"$CLAUDE_PROJECT_DIR"/guard.sh')]}]}
    extraction = extract_value("codex-hooks", {"hooks": fragment})
    assert extraction.value == fragment
    assert [n.kind for n in extraction.notes] == ["cannot"]
    assert "CLAUDE_PROJECT_DIR" in extraction.notes[0].detail
    with pytest.raises(LoadoutError):
        render_codex_hooks(extraction.value)


def test_the_symmetric_case_is_noted_for_claude() -> None:
    fragment = {"Stop": [{"hooks": [cmd("$CODEX_HOME/notify.sh")]}]}
    notes = extract_value("claude-hooks", {"hooks": fragment}).notes
    assert [n.kind for n in notes] == ["cannot"]


def test_hooks_in_a_file_codex_rejects_are_reported_not_extracted() -> None:
    """Codex parses hooks.json with `deny_unknown_fields`, so beside a key such as
    Cursor's `version` the file fails to parse and none of its hooks ran."""
    extraction = extract_value("codex-hooks", {"version": 1, "hooks": FRAGMENT})
    assert extraction.value == {}
    assert [n.kind for n in extraction.notes] == ["dropped"]
    assert "version" in extraction.notes[0].detail


def test_a_codex_description_is_noted_and_the_hooks_extracted() -> None:
    extraction = extract_value("codex-hooks", {"description": "audit", "hooks": FRAGMENT})
    assert extraction.value == FRAGMENT
    assert [n.kind for n in extraction.notes] == ["cannot"]


def test_droids_hooks_json_is_the_fragment_comment_keys_included() -> None:
    """Droid's file is the event map, so a comment key round-trips with the events
    rather than being reported as a key loadout does not write."""
    document = {"_comment": "audit", **FRAGMENT}
    extraction = extract_value("droid-hooks", document)
    assert extraction.notes == ()
    assert render_droid_hooks(extraction.value) == document


def test_droid_events_under_a_hooks_key_are_reported_and_not_extracted() -> None:
    """Droid reads a `hooks` key only in settings.json; in hooks.json those hooks
    never ran, so adopting them would switch on hooks the user never had running."""
    extraction = extract_value("droid-hooks", {"hooks": FRAGMENT, "Stop": FRAGMENT["Stop"]})
    assert extraction.value == {"Stop": FRAGMENT["Stop"]}
    assert [n.kind for n in extraction.notes] == ["dropped"]
    assert "never ran" in extraction.notes[0].detail


def test_claude_does_not_note_the_rest_of_settings_json() -> None:
    """`settings` is the residual slice there, so every other key has an owner —
    noting them would report a loss that does not happen."""
    assert extract_value("claude-hooks", claude_file(FRAGMENT)).notes == ()


def test_an_unrecognised_event_is_not_a_note() -> None:
    """The trap: an event Codex may not support round-trips perfectly, so a note
    would buy a silent exemption from property 2. It is reported at render time
    by `unrecognised_events`, where a user can act on it."""
    fragment = {"WorktreeCreate": [{"hooks": [cmd()]}]}
    extraction = extract_value("codex-hooks", {"hooks": fragment})
    assert extraction.notes == ()
    assert render_codex_hooks(extraction.value) == fragment


# --- registry ----------------------------------------------------------------


def test_every_value_renderer_has_a_value_extractor() -> None:
    """`VALUE_EXTRACTORS` also holds `claude-project-servers`, a `DocumentJsonSpec`
    rather than a `ValueSpec`. It belongs here anyway: both take a parsed JSON
    document as input, the contract this registry actually enforces (see the
    comment above it in extract.py) — unlike `codex-plugins`'s `DocumentTextSpec`,
    which takes TOML text and is excluded for exactly that reason."""
    value_renderers = {
        n
        for n, s in RENDERERS.items()
        if isinstance(s, ValueSpec | ValuesSpec | DocumentJsonSpec | MergedJsonSpec)
    }
    # A merged JSON spec can render from rules, as Cursor's `permissions` does; its
    # inverse is a document extractor in EXTRACTORS.
    assert value_renderers - NOT_INVERTED - set(EXTRACTORS) == set(VALUE_EXTRACTORS)


def test_an_unknown_name_is_an_error_rather_than_a_silent_empty() -> None:
    with pytest.raises(LoadoutError, match="no value extractor"):
        extract_value("claude", {})


def test_a_missing_hooks_key_extracts_empty_rather_than_failing() -> None:
    """A settings.json with no hooks is the common case, not an error."""
    assert extract_value("claude-hooks", {"model": "opus"}).value == {}
