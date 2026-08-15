"""What a generated adapter can carry, and what it must decline.

Claude and Codex are rendered declaratively and take every hook type. Only a
generated OpenCode/Pi adapter filters, because it spawns commands and a `prompt`
hook has none.
"""

from __future__ import annotations

from typing import Any

from loadout.hooks import (
    ADAPTABLE_TYPES,
    adaptable_document,
    render_claude_hooks,
    render_codex_hooks,
)


def cmd(command: str = "notify.sh") -> dict[str, Any]:
    return {"type": "command", "command": command, "timeout": 5}


PROMPT: dict[str, Any] = {
    "type": "prompt",
    "prompt": "A git commit is about to run. Evaluate the message.",
    "model": "haiku",
}
HTTP: dict[str, Any] = {"type": "http", "url": "http://127.0.0.1:8444/gate", "timeout": 5}


def test_only_command_hooks_are_adaptable() -> None:
    assert frozenset({"command"}) == ADAPTABLE_TYPES


def test_a_prompt_hook_reaches_claude_and_codex_untouched() -> None:
    """The gate is on adapters, not on the declarative harnesses."""
    document = {"PreToolUse": [{"matcher": "Bash", "hooks": [PROMPT]}]}
    assert render_claude_hooks(document) == document
    assert render_codex_hooks(document) == document


def test_a_prompt_hook_is_dropped_from_an_adapter_and_named() -> None:
    document = {"PreToolUse": [{"matcher": "Bash", "hooks": [cmd(), PROMPT]}]}
    kept, skipped = adaptable_document(document)
    assert [h["type"] for h in kept["PreToolUse"][0]["hooks"]] == ["command"]
    assert skipped == ("PreToolUse: prompt",)


def test_http_is_dropped_from_an_adapter_too() -> None:
    document = {"Stop": [{"hooks": [HTTP]}]}
    kept, skipped = adaptable_document(document)
    assert kept == {}
    assert skipped == ("Stop: http",)


def test_an_entry_left_with_no_hooks_is_dropped_not_emptied() -> None:
    """An adapter carrying an entry with zero hooks would subscribe to an event
    and do nothing."""
    document = {"PreToolUse": [{"matcher": "Bash", "hooks": [PROMPT]}, {"hooks": [cmd()]}]}
    kept, _ = adaptable_document(document)
    assert len(kept["PreToolUse"]) == 1
    assert "matcher" not in kept["PreToolUse"][0]


def test_filtering_does_not_mutate_the_input() -> None:
    document = {"PreToolUse": [{"hooks": [cmd(), PROMPT]}]}
    adaptable_document(document)
    assert len(document["PreToolUse"][0]["hooks"]) == 2


def test_a_comment_key_survives_filtering() -> None:
    """Comment keys hold a string, not a list; filtering must not choke on them."""
    document = {"_comment": "why", "Stop": [{"hooks": [cmd()]}]}
    kept, skipped = adaptable_document(document)
    assert kept["_comment"] == "why"
    assert skipped == ()


def test_every_skipped_hook_is_named_including_repeats() -> None:
    """Two prompt hooks on one event are two findings, not one — the report is
    what tells a user which hooks will not reach a harness."""
    document = {"PreToolUse": [{"hooks": [PROMPT, HTTP]}], "Stop": [{"hooks": [PROMPT]}]}
    _, skipped = adaptable_document(document)
    assert skipped == ("PreToolUse: prompt", "PreToolUse: http", "Stop: prompt")
