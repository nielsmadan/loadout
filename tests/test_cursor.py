"""Cursor CLI: the matcher forms, the missing ask list, the hook translation and the
one file that follows CURSOR_CONFIG_DIR. See docs/reference/cursor.md."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.destinations import resolve_destination
from loadout.discovery import live_roots
from loadout.errors import LoadoutError
from loadout.extract import extract, extract_value
from loadout.harnesses import GLOBAL_PRESET
from loadout.hooks import cursor_hooks, render_cursor_hooks
from loadout.notices import cursor_permission_notices
from loadout.permissions.renderers import (
    cursor_permissions,
    render_cursor,
    render_cursor_project,
)
from loadout.permissions.rules import Rules
from loadout.servers import Server, render_cursor_servers
from loadout.subagents import Agent, agent_notes, extract_agent, render_agent
from loadout.surgery import apply_json

CONFIG = GLOBAL_PRESET["cursor"]["permissions"].destination or ""


def command(script: str, **extra: object) -> dict[str, object]:
    return {"type": "command", "command": script, **extra}


# --- destinations ------------------------------------------------------------


def test_the_config_file_follows_cursor_config_dir_and_nothing_else_does() -> None:
    moved = {"HOME": "/h", "CURSOR_CONFIG_DIR": "/moved"}
    assert resolve_destination(CONFIG, "cursor", moved) == Path("/moved/cli-config.json")
    others = {
        name: resolve_destination(spec.destination or "", name, moved)
        for name, spec in GLOBAL_PRESET["cursor"].items()
        if name != "permissions"
    }
    assert others["mcp"] == Path("/h/.cursor/mcp.json")
    assert all(path.is_relative_to("/h/.cursor") for path in others.values())


def test_an_unset_variable_falls_back_to_xdg_config_home_before_the_home_directory() -> None:
    assert resolve_destination(CONFIG, "cursor", {"HOME": "/h", "XDG_CONFIG_HOME": "/x/"}) == Path(
        "/x/cursor/cli-config.json"
    )
    assert resolve_destination(CONFIG, "cursor", {"HOME": "/h"}) == Path(
        "/h/.cursor/cli-config.json"
    )


def test_discovery_maps_a_relocated_config_file_apart_from_the_cursor_root(tmp_path: Path) -> None:
    mappings = live_roots(tmp_path, {"CURSOR_CONFIG_DIR": str(tmp_path / "elsewhere")})
    cursor = [m for m in mappings if m.harnesses == ("cursor",)]
    assert [(m.kind, m.destination) for m in cursor] == [
        ("harness", tmp_path / ".cursor"),
        ("file", tmp_path / "elsewhere" / "cli-config.json"),
    ]


# --- permissions -------------------------------------------------------------


def test_each_entry_shape_renders_to_the_cursor_form_that_matches_it() -> None:
    rules = Rules(
        allow=("alpha", "beta sub", "gamma-*", "=mu status", "delta run --tag=*"),
        deny=("iota push *", "delta run --tag=*", "gamma-*", "=nu wipe"),
        mcp_allow=("svc/read",),
        mcp_deny=("blocked/*",),
    )
    assert cursor_permissions(rules) == {
        "allow": [
            "Shell(alpha)",
            "Shell(beta sub)",
            "Shell(gamma-*)",
            "Shell(mu:status)",
            "Shell(delta run --tag=*)",
            "Mcp(svc:read)",
        ],
        # A glob past the first word widens to its word prefix; one in the first word
        # stays, since Cursor also matches it against the first word alone.
        "deny": [
            "Shell(iota push)",
            "Shell(delta run)",
            "Shell(gamma-*)",
            "Shell(nu:wipe)",
            "Mcp(blocked:*)",
        ],
    }


def test_an_allow_reaching_an_ask_is_dropped_and_the_other_allows_stay() -> None:
    rules = Rules(
        allow=("git", "ls", "git log *"),
        ask=("git push",),
        mcp_allow=("svc/*", "other/*"),
        mcp_ask=("svc/write",),
    )
    assert cursor_permissions(rules)["allow"] == ["Shell(ls)", "Shell(git log *)", "Mcp(other:*)"]
    messages = [n.message for n in cursor_permission_notices("cursor", "cursor", rules)]
    assert sum("is not rendered because it would also approve" in m for m in messages) == 2
    assert sum(m.startswith("ask ") for m in messages) == 2


def test_global_scope_owns_permissions_and_keeps_what_the_cli_wrote() -> None:
    existing = json.dumps({"permissions": {"allow": ["Shell(old)"]}, "version": 1, "hints": True})
    fragment = json.dumps(render_cursor(Rules(allow=("ls",)), {}))
    merged = json.loads(apply_json(existing, frozenset({"permissions"}), fragment))
    assert merged == {
        "permissions": {"allow": ["Shell(ls)"], "deny": []},
        "version": 1,
        "hints": True,
    }
    assert list(merged) == ["permissions", "version", "hints"]


def test_project_scope_keeps_foreign_keys_in_place() -> None:
    document = render_cursor_project(Rules(deny=("rm",)), {"permissions": {}, "note": "kept"})
    assert document == {"permissions": {"allow": [], "deny": ["Shell(rm)"]}, "note": "kept"}


@pytest.mark.parametrize(
    ("token", "kind"),
    [("Read(.env*)", "lost"), ("Shell(curl:*)", "lost"), ("WebFetch(x.com)", "lost")],
)
def test_a_deny_init_cannot_carry_is_lost(token: str, kind: str) -> None:
    extraction = extract("cursor", {"permissions": {"allow": [], "deny": [token]}})
    assert extraction.rules.deny == ()
    assert [note.kind for note in extraction.notes] == [kind]


def test_an_allow_init_cannot_carry_is_noted_and_the_rest_is_adopted() -> None:
    extraction = extract(
        "cursor", {"permissions": {"allow": ["Read(src/**)", "Bash(ls)"], "deny": []}}
    )
    assert extraction.rules.allow == ("ls",)
    assert [note.kind for note in extraction.notes] == ["cannot"]


# --- hooks -------------------------------------------------------------------


def test_hooks_translate_as_cursor_translates_claude_hooks() -> None:
    document = {
        "PreToolUse": [
            {"matcher": "Bash|Edit|Write", "hooks": [command("guard.sh", timeout=5)]},
            {"matcher": "mcp__svc__write", "hooks": [command("mcp.sh")]},
        ],
        "UserPromptSubmit": [{"hooks": [{"type": "prompt", "prompt": "check it"}]}],
        "SessionStart": [{"matcher": "startup", "hooks": [command("start.sh")]}],
    }
    rendered = json.dumps(render_cursor_hooks(document), indent=2)
    assert json.loads(rendered) == {
        "version": 1,
        "hooks": {
            "preToolUse": [
                {
                    "type": "command",
                    "command": "guard.sh",
                    "loop_limit": None,
                    "failClosed": False,
                    "matcher": "Shell|Write",
                    "timeout": 5,
                },
                {
                    "type": "command",
                    "command": "mcp.sh",
                    "loop_limit": None,
                    "failClosed": False,
                    "matcher": "MCP:write",
                },
            ],
            "beforeSubmitPrompt": [
                {"type": "prompt", "prompt": "check it", "loop_limit": None, "failClosed": False}
            ],
            "sessionStart": [
                {"type": "command", "command": "start.sh", "loop_limit": None, "failClosed": False}
            ],
        },
    }


def test_what_cursor_has_no_place_for_is_reported() -> None:
    document = {
        "PreToolUse": [
            {"matcher": "Glob", "hooks": [command("glob.sh")]},
            {"matcher": "Bash", "hooks": [command("kept.sh")]},
        ],
        "Notification": [{"hooks": [command("notify.sh")]}],
        "Stop": [{"hooks": [{"type": "http", "url": "https://x"}]}],
    }
    rendered, skipped = cursor_hooks(document)
    assert [hook["command"] for hook in rendered["hooks"]["preToolUse"]] == ["kept.sh"]
    assert skipped == (
        "PreToolUse: matcher 'Glob' names no Cursor tool",
        "Notification: no Cursor event",
        "Stop: http",
    )


def test_cursor_defines_claudes_project_dir_and_no_other_claude_variable() -> None:
    render_cursor_hooks({"Stop": [{"hooks": [command('"$CLAUDE_PROJECT_DIR"/stop.sh')]}]})
    with pytest.raises(LoadoutError, match="CLAUDE_PLUGIN_ROOT"):
        render_cursor_hooks({"Stop": [{"hooks": [command("$CLAUDE_PLUGIN_ROOT/stop.sh")]}]})


def test_a_hook_extracted_from_cursor_renders_back_to_the_same_document() -> None:
    document = render_cursor_hooks(
        {"PreToolUse": [{"matcher": "Bash|Read", "hooks": [command("a.sh"), command("b.sh")]}]}
    )
    extraction = extract_value("cursor-hooks", document)
    assert extraction.notes == ()
    assert render_cursor_hooks(extraction.value) == document


@pytest.mark.parametrize(
    "hooks",
    [
        {"beforeShellExecution": [{"command": "x.sh"}]},
        {"stop": [{"command": "x.sh", "failClosed": True}]},
        {"preToolUse": [{"command": "x.sh", "matcher": "MCP:write"}]},
    ],
)
def test_a_cursor_hook_with_no_claude_form_is_lost(hooks: dict[str, object]) -> None:
    extraction = extract_value("cursor-hooks", {"version": 1, "hooks": hooks})
    assert extraction.value == {}
    assert [note.kind for note in extraction.notes] == ["lost"]


# --- servers and agents --------------------------------------------------------


def test_servers_reference_variables_in_cursors_own_syntax() -> None:
    servers = {
        "svc": Server("svc", "http", url="https://svc/mcp", auth_env_var="TOKEN"),
        "tools": Server("tools", "stdio", command="tools", env={"MODE": "x"}),
    }
    rendered = render_cursor_servers(servers)
    assert rendered == {
        "mcpServers": {
            "svc": {"url": "https://svc/mcp", "headers": {"Authorization": "Bearer ${env:TOKEN}"}},
            "tools": {"command": "tools", "args": [], "env": {"MODE": "x"}},
        }
    }
    assert extract_value("cursor-servers", rendered).value == servers


def test_an_agent_effort_is_noted_and_the_model_carries_it() -> None:
    agent = Agent(
        "rev",
        "Reviews.",
        "\nBody.\n",
        models={"cursor": "claude-opus-5[effort=high]"},
        effort="max",
    )
    assert render_agent(agent, "cursor").startswith(
        "---\nname: rev\ndescription: Reviews.\nmodel: claude-opus-5[effort=high]\n---\n"
    )
    (note,) = (note for harness, note in agent_notes(agent, ("cursor",)) if harness == "cursor")
    assert "give it in the model" in note


def test_a_one_line_description_is_written_plain_since_cursor_does_not_unquote() -> None:
    plain = Agent("rev", "Reviews: diffs.", "\nBody.\n")
    folded = Agent("rev", "Two\nlines.", "\nBody.\n")
    assert "\ndescription: Reviews: diffs.\n" in render_agent(plain, "cursor")
    assert '\ndescription: "Two\\nlines."\n' in render_agent(folded, "cursor")
    assert (
        extract_agent("cursor", render_agent(plain, "cursor"), "rev").description
        == "Reviews: diffs."
    )
