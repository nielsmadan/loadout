"""Harness-specific variables in hook commands.

The dominant real-world command shape is `"$CLAUDE_PROJECT_DIR"/script.sh` — 202
of 263 commands surveyed. Codex defines no project-root variable, so the same
string rendered there expands to nothing, runs a path that does not exist, and
exits 2 — the code both harnesses read as *block*.
"""

from __future__ import annotations

from typing import Any

import pytest

from loadout.errors import LoadoutError
from loadout.hooks import (
    HARNESS_PREFIXES,
    foreign_variables,
    render_claude_hooks,
    render_codex_hooks,
)


def cmd(command: str) -> dict[str, Any]:
    return {"hooks": [{"type": "command", "command": command, "timeout": 5}]}


CLAUDE_STYLE = {"PreToolUse": [cmd('"python" "$CLAUDE_PROJECT_DIR/.claude/hooks/guard.py"')]}


def test_a_harness_may_use_its_own_variables() -> None:
    """81% of real hooks look like this. Rendering to Claude must not complain."""
    assert render_claude_hooks(CLAUDE_STYLE) == CLAUDE_STYLE
    assert foreign_variables(CLAUDE_STYLE, "claude") == ()


def test_the_same_hook_is_rejected_for_codex() -> None:
    assert foreign_variables(CLAUDE_STYLE, "codex") == ("PreToolUse: $CLAUDE_PROJECT_DIR",)
    with pytest.raises(LoadoutError, match=r"CLAUDE_PROJECT_DIR"):
        render_codex_hooks(CLAUDE_STYLE)


def test_the_error_explains_the_block_rather_than_just_the_variable() -> None:
    """A user seeing "every edit is denied" needs the path from symptom to cause."""
    with pytest.raises(LoadoutError, match=r"exits 2"):
        render_codex_hooks(CLAUDE_STYLE)


def test_the_rejection_is_symmetric() -> None:
    document = {"Stop": [cmd("$CODEX_HOME/hooks/notify.sh")]}
    assert render_codex_hooks(document) == document
    with pytest.raises(LoadoutError, match=r"CODEX_HOME"):
        render_claude_hooks(document)


def test_the_braced_form_is_caught() -> None:
    document = {"SessionStart": [cmd('"${CLAUDE_PLUGIN_ROOT}/hooks/run.cmd" start')]}
    assert foreign_variables(document, "codex") == ("SessionStart: $CLAUDE_PLUGIN_ROOT",)


def test_a_portable_command_passes_to_both() -> None:
    """Relative paths and inline shell are how 49 of 259 surveyed hooks stay
    portable — the established style this check pushes people towards."""
    document = {"Stop": [cmd("node scripts/notify.mjs")]}
    assert render_claude_hooks(document) == document
    assert render_codex_hooks(document) == document


def test_an_unrelated_variable_is_not_a_harness_variable() -> None:
    """`$HOME` and friends belong to nobody and are defined everywhere."""
    document = {"Stop": [cmd('"$HOME"/.local/bin/notify.sh')]}
    assert foreign_variables(document, "codex") == ()
    assert render_codex_hooks(document) == document


def test_variables_are_found_outside_the_command_field() -> None:
    document = {
        "PreToolUse": [{"hooks": [{"type": "http", "url": "http://x/$CLAUDE_PROJECT_DIR"}]}]
    }
    assert foreign_variables(document, "codex") == ("PreToolUse: $CLAUDE_PROJECT_DIR",)


def test_repeats_are_named_once_in_the_message_but_all_are_found() -> None:
    document = {"PreToolUse": [cmd("$CLAUDE_PROJECT_DIR/a.sh"), cmd("$CLAUDE_PROJECT_DIR/b.sh")]}
    assert len(foreign_variables(document, "codex")) == 2
    with pytest.raises(LoadoutError) as raised:
        render_codex_hooks(document)
    assert str(raised.value).count("$CLAUDE_PROJECT_DIR") == 1


def test_a_comment_key_does_not_break_the_scan() -> None:
    document = {"_comment": "uses $CLAUDE_PROJECT_DIR", "Stop": [cmd("plain.sh")]}
    assert foreign_variables(document, "codex") == ()


def test_every_supported_harness_has_a_namespace() -> None:
    assert {"claude", "codex"} == set(HARNESS_PREFIXES)
