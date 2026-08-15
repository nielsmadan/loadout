"""The hooks slice against hook documents people actually wrote.

Shapes are taken from `~/ac/loadout/settings/claude.json` (11 events, 15
commands) and the `superpowers` plugin's `hooks/hooks.json`. Commands are
rewritten to placeholders — what is under test is the *shape* a real document
has, not one machine's paths.
"""

from __future__ import annotations

from typing import Any

from loadout.documents import merge_documents
from loadout.hooks import CODEX_EVENTS, render_claude_hooks, render_codex_hooks, unrecognised_events

NOTIFY = "~/.claude/hooks/juggler/notify.sh"


def notify(event: str, matcher: str | None = None) -> dict[str, Any]:
    """The commonest real hook: one entry per event, the event name repeated as
    an argument to a single script."""
    entry: dict[str, Any] = {
        "hooks": [{"type": "command", "command": f"{NOTIFY} {event}", "timeout": 5}]
    }
    if matcher:
        entry["matcher"] = matcher
    return entry


def script(path: str, matcher: str) -> dict[str, Any]:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": path, "timeout": 5}]}


def test_a_real_eleven_event_document_renders_to_both_harnesses() -> None:
    document = {
        "PostToolUseFailure": [
            script("enforce-fix-failures.sh", "Bash"),
            notify("PostToolUseFailure", "*"),
        ],
        "PreToolUse": [
            script("redirect-gh-api.sh", "Bash"),
            script("nudge-jina.sh", "WebFetch"),
            notify("PreToolUse", "*"),
        ],
        "PermissionRequest": [
            script("auto-approve-mcp.sh", "mcp__*"),
            notify("PermissionRequest", "*"),
        ],
        "SessionStart": [notify("SessionStart")],
        "Stop": [notify("Stop")],
    }
    assert render_claude_hooks(document) == document
    assert render_codex_hooks(document) == document


def test_the_events_codex_is_not_known_to_support_are_reported_not_dropped() -> None:
    """`PostToolUseFailure` and `StopFailure` are in Claude's list and not in the
    established part of Codex's. Both still render — see `unrecognised_events`."""
    document = {
        "PostToolUseFailure": [notify("PostToolUseFailure")],
        "StopFailure": [notify("StopFailure")],
    }
    assert unrecognised_events(document, CODEX_EVENTS) == ("PostToolUseFailure", "StopFailure")
    assert set(render_codex_hooks(document)) == {"PostToolUseFailure", "StopFailure"}


def test_two_fragments_adding_to_one_event_produce_both_entries() -> None:
    """The composition case the slice exists for: a base fragment wires
    notification, a second adds a targeted hook to the same event."""
    base = {"PreToolUse": [notify("PreToolUse", "*")]}
    delta = {"PreToolUse": [script("nudge-jina.sh", "WebFetch")]}
    merged = merge_documents(base, delta)
    assert [e.get("matcher") for e in merged["PreToolUse"]] == ["*", "WebFetch"]
    assert render_claude_hooks(merged) == merged


def test_a_profile_can_drop_a_whole_event_but_not_one_entry() -> None:
    """`None` removes a key, which is event granularity. Dropping one entry of
    several means splitting the fragment — there is no element-removal syntax
    (spec 1 §8)."""
    base = {"PreToolUse": [notify("PreToolUse", "*")], "Stop": [notify("Stop")]}
    assert merge_documents(base, {"Stop": None}) == {"PreToolUse": base["PreToolUse"]}


def test_superpowers_carries_entry_fields_beyond_type_command_timeout() -> None:
    """`shell` and `async` are real and undocumented in our spec; they must
    survive rather than be normalised away."""
    document = {
        "SessionStart": [
            {
                "matcher": "startup|clear|compact",
                "hooks": [
                    {
                        "type": "command",
                        "command": '"${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd" session-start',
                        "shell": "bash",
                        "async": False,
                    }
                ],
            }
        ]
    }
    rendered = render_claude_hooks(document)
    hook = rendered["SessionStart"][0]["hooks"][0]
    assert hook["shell"] == "bash" and hook["async"] is False
    assert "${CLAUDE_PLUGIN_ROOT}" in hook["command"]


def test_a_matcher_means_different_things_on_different_events() -> None:
    """`PreToolUse` matches a tool name, `SessionStart` matches a start reason.
    The renderer must not interpret either — it passes matchers through."""
    document = {
        "PreToolUse": [script("a.sh", "Bash")],
        "SessionStart": [script("b.sh", "startup|clear|compact")],
    }
    rendered = render_claude_hooks(document)
    assert rendered["PreToolUse"][0]["matcher"] == "Bash"
    assert rendered["SessionStart"][0]["matcher"] == "startup|clear|compact"
