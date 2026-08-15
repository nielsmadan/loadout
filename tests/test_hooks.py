from __future__ import annotations

from typing import Any

from loadout.hooks import (
    CLAUDE_EVENTS,
    CODEX_EVENTS,
    render_claude_hooks,
    render_codex_hooks,
    unrecognised_events,
)


def entry(command: str, matcher: str | None = None) -> dict[str, Any]:
    hook = {"type": "command", "command": command, "timeout": 5}
    return {"matcher": matcher, "hooks": [hook]} if matcher else {"hooks": [hook]}


def test_claude_render_is_the_abi_document_itself() -> None:
    """The ABI is Claude's protocol, so its renderer translates nothing."""
    document = {"PreToolUse": [entry("notify.sh", "Bash")]}
    assert render_claude_hooks(document) == document


def test_claude_render_does_not_mutate_its_input() -> None:
    document: dict[str, Any] = {"PreToolUse": [entry("notify.sh")]}
    rendered = render_claude_hooks(document)
    rendered["PreToolUse"].append(entry("other.sh"))
    assert len(document["PreToolUse"]) == 1


def test_codex_wraps_the_document_under_a_hooks_key() -> None:
    """Codex's file is `{"hooks": {...}}`; Claude's is the map itself."""
    document = {"PostToolUse": [entry("notify.sh")]}
    assert render_codex_hooks(document) == document


def test_event_order_is_preserved_for_both() -> None:
    """Key order is load-bearing in generated output (AGENTS.md)."""
    document = {"Stop": [entry("a.sh")], "PreToolUse": [entry("b.sh")]}
    assert list(render_claude_hooks(document)) == ["Stop", "PreToolUse"]
    assert list(render_codex_hooks(document)) == ["Stop", "PreToolUse"]


def test_an_unrecognised_event_is_reported_for_the_harness_that_lacks_it() -> None:
    document = {"WorktreeCreate": [entry("a.sh")], "PreToolUse": [entry("b.sh")]}
    assert unrecognised_events(document, CODEX_EVENTS) == ("WorktreeCreate",)
    assert unrecognised_events(document, CLAUDE_EVENTS) == ()


def test_an_unrecognised_event_is_still_emitted() -> None:
    """Skipping would be wrong: no harness's event list is established as
    complete, so an event missing from ours may still be supported. Emitting a
    key the harness ignores is recoverable; dropping one it honours is not."""
    document = {"WorktreeCreate": [entry("a.sh")]}
    assert "WorktreeCreate" in render_codex_hooks(document)


def test_unrecognised_events_are_reported_in_document_order() -> None:
    document = {"StopFailure": [entry("a.sh")], "PreToolUse": [], "Notification": [entry("c.sh")]}
    assert unrecognised_events(document, CODEX_EVENTS) == ("StopFailure", "Notification")


def test_a_comment_key_survives_and_is_not_reported_as_an_event() -> None:
    """JSON has no comments, so real documents carry them as keys — `_comment`
    and `// PostToolUse hooks` both appear in public configurations. An earlier
    version rejected these as malformed."""
    document = {"_comment": "why these hooks exist", "PreToolUse": [entry("a.sh")]}
    assert render_claude_hooks(document) == document
    assert unrecognised_events(document, CLAUDE_EVENTS) == ()


def test_hook_types_without_a_command_survive() -> None:
    """`http` and `prompt` hooks carry no command at all. The spec called the
    portable unit "a command"; two of 22 public configurations disagree."""
    document = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "http", "url": "http://127.0.0.1:8444/gate", "timeout": 5},
                    {"type": "prompt", "prompt": "Evaluate the commit message.", "model": "haiku"},
                ],
            }
        ]
    }
    assert render_claude_hooks(document) == document


def test_codex_events_are_a_subset_of_claude_events_as_far_as_established() -> None:
    """Every Codex event found is also in Claude's list. This is a fact about
    both extractions, not a proof that Codex lacks the rest — see config.md."""
    assert CODEX_EVENTS <= CLAUDE_EVENTS


def test_claude_event_list_carries_the_events_the_binary_documents() -> None:
    """A sample rather than the whole list: enough to catch a truncated table,
    and the four beyond the eleven that a config file would have shown."""
    for event in ("PreToolUse", "PermissionRequest", "PermissionDenied", "Setup", "TeammateIdle"):
        assert event in CLAUDE_EVENTS
    assert len(CLAUDE_EVENTS) == 31
