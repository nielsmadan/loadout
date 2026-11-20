"""Devin CLI: word-prefix and exact command rules, the ask an allow can beat, the hook
translation, and the config directory under XDG_CONFIG_HOME. See docs/reference/devin.md."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import for_destination
from loadout.destinations import resolve_destination
from loadout.discovery import live_roots
from loadout.errors import LoadoutError
from loadout.extract import extract, extract_value
from loadout.harnesses import GLOBAL_PRESET
from loadout.hooks import devin_hooks, render_devin_hooks
from loadout.notices import (
    devin_claude_import,
    devin_overridden,
    devin_permission_notices,
    notices_for,
)
from loadout.permissions.renderers import devin_permissions, render_devin_project
from loadout.permissions.rules import Rules
from loadout.servers import Server, render_devin_servers
from loadout.subagents import Agent, agent_notes, render_agent

CONFIG = GLOBAL_PRESET["devin"]["permissions"].destination or ""


def command(script: str, **extra: object) -> dict[str, object]:
    return {"type": "command", "command": script, **extra}


# --- destinations ------------------------------------------------------------


def test_every_global_file_follows_xdg_config_home_into_a_devin_folder() -> None:
    moved = {"HOME": "/h", "XDG_CONFIG_HOME": "/x"}
    resolved = {
        name: resolve_destination(spec.destination or "", name, moved)
        for name, spec in GLOBAL_PRESET["devin"].items()
    }
    assert resolved["permissions"] == Path("/x/devin/config.json")
    assert resolved["mcp"] == Path("/x/devin/mcp_config.json")
    assert resolved["instructions"] == Path("/x/devin/AGENTS.md")
    assert all(path.is_relative_to("/x/devin") for path in resolved.values())
    assert resolve_destination(CONFIG, "devin", {"HOME": "/h"}) == Path(
        "/h/.config/devin/config.json"
    )


def test_discovery_finds_the_devin_root_under_the_xdg_folder(tmp_path: Path) -> None:
    mappings = live_roots(tmp_path, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")})
    devin = [m for m in mappings if m.harnesses == ("devin",)]
    assert [(m.kind, m.destination, m.destination_template) for m in devin] == [
        ("harness", tmp_path / "xdg" / "devin", "${XDG_CONFIG_HOME:-~/.config}/devin")
    ]


# --- permissions -------------------------------------------------------------


def test_each_entry_shape_renders_to_the_devin_rule_that_matches_it() -> None:
    rules = Rules(
        allow=("alpha", "beta sub", "gamma-*", "=mu status"),
        ask=("theta check", "kappa run *"),
        deny=("iota push", "=nu wipe", "lambda rm --force=*"),
        mcp_allow=("svc/read",),
        mcp_deny=("blocked/*",),
    )
    assert devin_permissions(rules) == {
        "allow": ["Exec(alpha)", "Exec(beta sub)", "ExecExact(mu status)", "mcp__svc__read"],
        "ask": ["Exec(theta check)", "Exec(kappa run)"],
        "deny": ["Exec(iota push)", "ExecExact(nu wipe)", "Exec(lambda rm)", "mcp__blocked__*"],
    }


@pytest.mark.parametrize("category", ["ask", "deny"])
def test_a_glob_inside_the_first_word_is_refused_in_a_list_that_restricts(category: str) -> None:
    with pytest.raises(LoadoutError, match="Devin matches whole words"):
        devin_permissions(Rules(**{category: ("gamma-*",)}))


def test_an_allow_inside_an_ask_is_dropped_and_a_broader_one_stays() -> None:
    """Devin lets an allow at least as specific as an ask win, so the narrower allows
    would approve what the source asks about."""
    rules = Rules(
        allow=("git", "git push --dry-run", "=git push", "ls"),
        ask=("git push",),
        mcp_allow=("svc/*", "svc/write", "other/read"),
        mcp_ask=("svc/write",),
    )
    assert devin_permissions(rules)["allow"] == [
        "Exec(git)",
        "Exec(ls)",
        "mcp__svc__*",
        "mcp__other__read",
    ]
    messages = [n.message for n in devin_permission_notices("devin", "devin", rules)]
    assert sum("would approve what the source asks about" in m for m in messages) == 3


def test_a_tool_allow_under_a_server_wide_ask_is_dropped() -> None:
    rules = Rules(mcp_allow=("svc/read", "other/read"), mcp_ask=("svc/*",))
    assert devin_permissions(rules)["allow"] == ["mcp__other__read"]


@pytest.mark.parametrize(
    ("default", "lists"),
    [
        ("allow", {"allow": ["Exec(ls)", "Exec(*)"], "ask": [], "deny": []}),
        ("ask", {"allow": ["Exec(ls)"], "ask": ["Exec(*)"], "deny": []}),
        ("deny", {"allow": ["Exec(ls)"], "ask": ["Exec(*)"], "deny": []}),
    ],
)
def test_a_stated_catch_all_is_exec_star_in_the_list_closest_to_it(
    default: str, lists: dict[str, list[str]]
) -> None:
    assert devin_permissions(Rules(allow=("ls",), default=default)) == lists  # type: ignore[arg-type]


def test_a_catch_all_deny_is_reported_as_rendered_as_ask() -> None:
    messages = [
        n.message for n in devin_permission_notices("devin", "devin", Rules(default="deny"))
    ]
    assert messages == [
        (
            '[shell] default = "deny" is rendered as an ask of every command, because Devin '
            "cannot deny what no rule names without also denying what the source allows"
        )
    ]


def test_project_scope_keeps_the_files_other_keys() -> None:
    base = {"read_config_from": {"claude": False}, "requiredPlugins": ["acme/tools"]}
    assert render_devin_project(Rules(deny=("rm",)), base) == {
        "read_config_from": {"claude": False},
        "requiredPlugins": ["acme/tools"],
        "permissions": {"allow": [], "ask": [], "deny": ["Exec(rm)"]},
    }


def test_extraction_reads_exec_star_and_a_bare_exec_as_the_catch_all() -> None:
    document = {"permissions": {"allow": ["Exec(ls)"], "ask": ["exec"], "deny": ["Exec(rm)"]}}
    assert extract("devin", document).rules == Rules(allow=("ls",), deny=("rm",), default="ask")


def test_extraction_refuses_to_drop_a_deny_it_cannot_carry() -> None:
    document = {"permissions": {"deny": ["Read(secret.txt)"], "allow": ["Fetch(domain:x.io)"]}}
    kinds = sorted(note.kind for note in extract("devin", document).notes)
    assert kinds == ["cannot", "lost"]


# --- the user-level allows that override a project rule ----------------------


def test_a_user_allow_reaching_a_project_deny_is_reported() -> None:
    rules = Rules(deny=("git push",), ask=("npm publish",))
    user = {"permissions": {"allow": ["Exec(git)", "Exec(ls)"]}}
    messages = [n.message for n in devin_overridden(rules, user)]
    assert len(messages) == 1
    assert messages[0].startswith(
        "deny 'Exec(git push)' in .devin/config.json does not apply where 'Exec(git)'"
    )


def test_user_dangerous_mode_is_reported_once_for_the_whole_project() -> None:
    rules = Rules(deny=("git push",), ask=("npm publish",))
    messages = [n.message for n in devin_overridden(rules, {"permission_mode": "yolo"})]
    assert len(messages) == 1
    assert "permission_mode 'yolo'" in messages[0]


def test_an_mcp_server_allow_overrides_a_tool_deny() -> None:
    rules = Rules(mcp_deny=("svc/purge",))
    user = {"permissions": {"allow": ["mcp__svc__*"]}}
    assert [n.message.split(" in ")[0] for n in devin_overridden(rules, user)] == [
        "deny 'mcp__svc__purge'"
    ]


# --- hooks -------------------------------------------------------------------


def test_claude_tool_matchers_become_anchored_devin_names() -> None:
    document = {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [command("guard.sh")]},
            {"matcher": "Edit|Write", "hooks": [command("fmt.sh", timeout=5)]},
            {"matcher": "mcp__github__.*", "hooks": [command("audit.sh")]},
            {"matcher": "*", "hooks": [command("all.sh")]},
        ],
        "Stop": [{"hooks": [{"type": "prompt", "prompt": "done?"}]}],
    }
    assert render_devin_hooks(document) == {
        "PreToolUse": [
            {"matcher": "^exec$", "hooks": [command("guard.sh")]},
            {"matcher": "^(edit|apply_patch|write)$", "hooks": [command("fmt.sh", timeout=5)]},
            {"matcher": "^mcp__github__.*$", "hooks": [command("audit.sh")]},
            {"matcher": "", "hooks": [command("all.sh")]},
        ],
        "Stop": [{"hooks": [{"type": "prompt", "prompt": "done?"}]}],
    }


def test_an_http_hook_and_a_matcher_on_an_event_without_tools_are_left_out() -> None:
    document = {
        "PostToolUse": [{"hooks": [{"type": "http", "url": "https://x"}, command("log.sh")]}],
        "SessionStart": [{"matcher": "startup", "hooks": [command("hello.sh")]}],
    }
    rendered, skipped = devin_hooks(document)
    assert rendered == {
        "PostToolUse": [{"hooks": [command("log.sh")]}],
        "SessionStart": [{"hooks": [command("hello.sh")]}],
    }
    assert skipped == (
        "PostToolUse: http",
        "SessionStart: matcher 'startup', which Devin has none of here",
    )


def test_a_claude_variable_is_refused_because_devin_does_not_set_it() -> None:
    document = {"PreToolUse": [{"hooks": [command('"$CLAUDE_PROJECT_DIR"/guard.sh')]}]}
    with pytest.raises(LoadoutError, match="devin does not define"):
        render_devin_hooks(document)


def test_rendered_hooks_extract_back_to_the_claude_document() -> None:
    document = {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [command("guard.sh")]},
            {"matcher": "Edit|Task", "hooks": [command("x.sh")]},
        ],
        "UserPromptSubmit": [{"hooks": [command('"$DEVIN_PROJECT_DIR"/note.sh')]}],
    }
    rendered = render_devin_hooks(document)
    project = extract_value("devin-project-hooks", rendered)
    merged = extract_value("devin-hooks", {"version": 1, "hooks": rendered})
    assert project.value == document == merged.value
    assert project.notes == ()


def test_an_unanchored_matcher_written_by_hand_is_lost_on_adoption() -> None:
    extraction = extract_value(
        "devin-project-hooks", {"PreToolUse": [{"matcher": "exec", "hooks": [command("a")]}]}
    )
    assert extraction.value == {}
    assert [note.kind for note in extraction.notes] == ["lost"]


def test_an_event_devin_never_loads_is_reported() -> None:
    document = {"Notification": [{"hooks": [command("ping.sh")]}]}
    messages = [n.message for n in notices_for("devin", "hooks", document)]
    assert messages == ["Notification is not an event Devin loads, so it will not fire"]


# --- servers -----------------------------------------------------------------


def test_servers_carry_transport_and_an_env_reference_devin_expands() -> None:
    servers = {
        "local": Server(name="local", transport="stdio", command="srv", args=("-v",)),
        "remote": Server(
            name="remote", transport="http", url="https://x/mcp", auth_env_var="TOKEN"
        ),
    }
    assert render_devin_servers(servers) == {
        "mcpServers": {
            "local": {"command": "srv", "args": ["-v"], "transport": "stdio"},
            "remote": {
                "url": "https://x/mcp",
                "transport": "http",
                "headers": {"Authorization": "Bearer ${env:TOKEN}"},
            },
        }
    }
    extracted = extract_value("devin-servers", render_devin_servers(servers))
    assert extracted.value == servers
    assert extracted.notes == ()


# --- agents and rules ----------------------------------------------------------


def test_an_agent_gets_no_effort_and_a_note_says_so() -> None:
    agent = Agent("reviewer", "Reviews code", "Body.\n", models={"devin": "swe"}, effort="high")
    rendered = render_agent(agent, "devin")
    assert rendered.startswith('---\nname: reviewer\ndescription: "Reviews code"\nmodel: swe\n---')
    assert agent_notes(agent, ("devin",)) == (
        (
            "devin",
            (
                "agent 'reviewer': Devin agents have no effort field, so effort 'high' is not "
                "written for Devin"
            ),
        ),
    )


def test_a_rule_in_a_devin_rules_folder_starts_with_always_on_frontmatter() -> None:
    assert for_destination(Path("/r/.devin/rules/team.md"), "# Team\n") == (
        "---\ntrigger: always_on\n---\n\n# Team\n"
    )
    assert for_destination(Path("/r/docs/rules/team.md"), "# Team\n") == "# Team\n"


def test_an_sse_server_is_lost_on_adoption() -> None:
    document = {"mcpServers": {"old": {"url": "https://x/sse", "transport": "sse"}}}
    extraction = extract_value("devin-servers", document)
    assert extraction.value == {}
    assert [(n.kind, n.detail) for n in extraction.notes] == [
        ("lost", "devin-servers: old is a sse server")
    ]


def test_both_harnesses_declared_reports_the_claude_import_until_it_is_off() -> None:
    assert [n.message[:50] for n in devin_claude_import(("claude", "devin"), {})] == [
        "Devin also reads Claude's instructions, skills and"
    ]
    assert devin_claude_import(("claude", "devin"), {"read_config_from": {"claude": False}}) == ()
    assert devin_claude_import(("devin",), {}) == ()
