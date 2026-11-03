"""Init refuses a value it cannot carry when dropping it would change what the harness does,
and notes one whose drop changes nothing.

The user's ruling on review question 1: a server losing a key it runs with, a header holding a
credential reference, or a transport the source cannot say is a change in behaviour, so it is
an issue like an uncarried permission rule (ADR 0033). An entry the harness never used, or
one it cannot start with, is a note.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.init_workflow import SKILL_ADVICE
from loadout.migration import DROID_INERT_SETTINGS_HOOKS

pytestmark = pytest.mark.migration_integration


def _git(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _project(root: Path, harness: str, capsys) -> tuple[int, dict]:
    argv = ["init", "project", "--root", str(root), "--harness", harness, "--dry-run", "--json"]
    code = loadout.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _global(source: Path, harness: str, capsys) -> tuple[int, dict]:
    argv = ["init", "global", str(_git(source)), "--harness", harness, "--dry-run", "--json"]
    code = loadout.main(argv)
    return code, json.loads(capsys.readouterr().out)


def _refused(code: int, preview: dict, original: Path, *fragments: str) -> str:
    assert code == 2
    assert [(i["code"], i["paths"]) for i in preview["issues"]] == [
        ("uncarried-setting", [str(original)])
    ]
    message: str = preview["issues"][0]["message"]
    assert all(fragment in message for fragment in fragments)
    assert preview["recommendation"] == SKILL_ADVICE
    return message


def _converts(code: int, preview: dict, note: str) -> None:
    assert code == 0
    assert preview["issues"] == []
    assert any(note in line for line in preview["notes"])


def _http(**extra: object) -> dict[str, object]:
    return {"type": "http", "url": "https://x.test/mcp", **extra}


# --- refused: the harness would run differently --------------------------------------


def test_a_claude_project_server_key_the_source_cannot_hold(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    servers = {"mcpServers": {"docs": {"command": "docs-mcp", "timeout": 30}}}
    _write(root / ".mcp.json", json.dumps(servers))

    _refused(*_project(root, "claude", capsys), root / ".mcp.json", "docs carries timeout")


def test_one_consequence_is_stated_once_for_every_server_it_follows_from(
    tmp_path: Path, capsys
) -> None:
    root = _git(tmp_path / "p")
    servers = {"a": {"command": "a", "timeout": 1}, "b": {"command": "b", "cwd": "/"}}
    _write(root / ".mcp.json", json.dumps({"mcpServers": servers}))

    message = _refused(*_project(root, "claude", capsys), root / ".mcp.json")

    assert (
        "claude-servers: a carries timeout, claude-servers: b carries cwd: "
        "the harness would run the server without them."
    ) in message


def test_a_header_holding_a_reference(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    entry = _http(headers={"X-Key": "${KEY}"})
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"docs": entry}}))

    _refused(*_project(root, "claude", capsys), root / ".mcp.json", "header X-Key")


def test_a_claude_sse_server(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    entry = {"type": "sse", "url": "https://x.test/sse"}
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"docs": entry}}))

    _refused(*_project(root, "claude", capsys), root / ".mcp.json", "docs is a sse server")


def test_droid_remembered_tool_approvals(tmp_path: Path, fake_home: Path, capsys) -> None:
    mcp = fake_home / ".factory/mcp.json"
    _write(mcp, json.dumps({"mcpServers": {}, "persistentPermissions": {"docs": ["search"]}}))

    _refused(*_global(tmp_path / "source", "droid", capsys), mcp, "holds persistentPermissions")


def test_an_opencode_server_switched_off(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    entry = {"type": "local", "command": ["docs-mcp"], "enabled": False}
    _write(root / "opencode.json", json.dumps({"mcp": {"docs": entry}}))

    _refused(*_project(root, "opencode", capsys), root / "opencode.json", "docs carries enabled")


def test_an_opencode_document_gives_its_rule_and_setting_refusals_together(
    tmp_path: Path, capsys
) -> None:
    """The permission refusal used to end the run before the server was read."""
    root = _git(tmp_path / "p")
    entry = {"type": "local", "command": ["docs-mcp"], "enabled": False}
    document = {"permission": {"bash": {"ls": "yolo"}}, "mcp": {"docs": entry}}
    _write(root / "opencode.json", json.dumps(document))

    code, preview = _project(root, "opencode", capsys)

    assert code == 2
    assert [(i["code"], i["paths"]) for i in preview["issues"]] == [
        ("uncarried-rule", [str(root / "opencode.json")]),
        ("uncarried-setting", [str(root / "opencode.json")]),
    ]
    assert "decision 'yolo' for 'ls'" in preview["issues"][0]["message"]
    assert "docs carries enabled" in preview["issues"][1]["message"]


def test_a_pi_server_switched_off(tmp_path: Path, fake_home: Path, capsys) -> None:
    mcp = fake_home / ".pi/agent/mcp.json"
    _write(mcp, json.dumps({"mcpServers": {"docs": {"command": "docs-mcp", "enabled": False}}}))

    _refused(*_global(tmp_path / "source", "pi", capsys), mcp, "docs carries enabled")


def test_a_pi_authorization_header_beside_adapter_bearer_auth(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    mcp = fake_home / ".pi/agent/mcp.json"
    entry = {
        "url": "https://x.test/mcp",
        "auth": "bearer",
        "bearerTokenEnv": "TOKEN",
        "headers": {"Authorization": "Bearer abc"},
    }
    _write(mcp, json.dumps({"mcpServers": {"docs": entry}}))

    _refused(*_global(tmp_path / "source", "pi", capsys), mcp, "beside auth: bearer")


def test_pi_document_settings_beside_its_servers(tmp_path: Path, fake_home: Path, capsys) -> None:
    mcp = fake_home / ".pi/agent/mcp.json"
    _write(mcp, json.dumps({"mcpServers": {}, "autoEnableCodemode": False}))

    _refused(*_global(tmp_path / "source", "pi", capsys), mcp, "holds autoEnableCodemode")


def test_a_pi_override_entry(tmp_path: Path, fake_home: Path, capsys) -> None:
    mcp = fake_home / ".pi/agent/mcp.json"
    _write(mcp, json.dumps({"mcpServers": {"shared": {"disabled": True}}}))

    _refused(*_global(tmp_path / "source", "pi", capsys), mcp, "shared has neither url nor command")


def test_a_codex_server_key_neither_servers_nor_policy_hold(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    config = fake_home / ".codex/config.toml"
    _write(config, '[mcp_servers.docs]\ncommand = "docs-mcp"\nenabled_tools = ["search"]\n')

    _refused(*_global(tmp_path / "source", "codex", capsys), config, "docs carries enabled_tools")


def test_a_codex_writes_approval_mode(tmp_path: Path, fake_home: Path, capsys) -> None:
    config = fake_home / ".codex/config.toml"
    _write(
        config, '[mcp_servers.docs]\ncommand = "docs-mcp"\ndefault_tools_approval_mode = "writes"\n'
    )

    _refused(*_global(tmp_path / "source", "codex", capsys), config, "approval mode 'writes'")


def test_a_codex_server_key_and_approval_mode_are_refused_in_one_run(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Both come from config.toml, read one after the other; the first used to end the run."""
    config = fake_home / ".codex/config.toml"
    _write(
        config,
        '[mcp_servers.docs]\ncommand = "docs-mcp"\nenabled_tools = ["search"]\n\n'
        '[mcp_servers.web]\ncommand = "web-mcp"\ndefault_tools_approval_mode = "writes"\n',
    )

    code, preview = _global(tmp_path / "source", "codex", capsys)

    assert code == 2
    messages = [i["message"] for i in preview["issues"] if i["paths"] == [str(config)]]
    assert len(messages) == 2
    assert "docs carries enabled_tools" in messages[0]
    assert "approval mode 'writes'" in messages[1]


def test_a_codex_plugin_s_server_policy(tmp_path: Path, fake_home: Path, capsys) -> None:
    config = fake_home / ".codex/config.toml"
    _write(
        config,
        '[plugins."lint@tools"]\nenabled = true\n\n'
        '[plugins."lint@tools".mcp_servers.lint]\nenabled = false\n',
    )

    _refused(*_global(tmp_path / "source", "codex", capsys), config, "carries mcp_servers")


def test_claude_plugins_that_are_not_a_map(tmp_path: Path, fake_home: Path, capsys) -> None:
    settings = fake_home / ".claude/settings.json"
    _write(settings, json.dumps({"enabledPlugins": ["lint@tools"], "model": "opus"}))

    _refused(*_global(tmp_path / "source", "claude", capsys), settings, "not a map")


# --- carried: nothing is lost ----------------------------------------------------------


def test_codex_server_policy_is_carried_as_mcp_rules(tmp_path: Path, fake_home: Path) -> None:
    config = fake_home / ".codex/config.toml"
    _write(
        config,
        '[mcp_servers.docs]\ncommand = "docs-mcp"\nenabled = false\n\n'
        '[mcp_servers.web]\nurl = "https://w.test/mcp"\ndefault_tools_approval_mode = "prompt"\n\n'
        '[mcp_servers.web.tools.search]\napproval_mode = "approve"\n',
    )
    source = _git(tmp_path / "source")

    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0

    rules = tomllib.loads((source / "loadout/permissions.toml").read_text())["mcp"]
    assert rules == {"allow": ["web/search"], "ask": ["web/*"], "deny": ["docs/*"]}
    rendered = tomllib.loads(config.read_text())["mcp_servers"]
    assert rendered["docs"]["enabled"] is False
    assert rendered["web"]["tools"]["search"]["approval_mode"] == "approve"
    assert loadout.main(["check", "--global"]) == 0


def test_a_claude_plugin_switched_off_is_carried(tmp_path: Path, fake_home: Path) -> None:
    settings = fake_home / ".claude/settings.json"
    _write(settings, json.dumps({"enabledPlugins": {"x@m": False, "y@m": True}}))
    source = _git(tmp_path / "source")

    assert loadout.main(["init", "global", str(source), "--harness", "claude", "--yes"]) == 0

    fragment = json.loads((source / "loadout/plugins/claude.json").read_text())
    assert fragment["plugins"] == {
        "x": {"marketplace": "m", "enabled": False},
        "y": {"marketplace": "m"},
    }
    assert json.loads(settings.read_text())["enabledPlugins"] == {"x@m": False, "y@m": True}
    assert loadout.main(["check", "--global"]) == 0


def test_a_droid_project_plugin_switched_off_is_carried(tmp_path: Path) -> None:
    root = _git(tmp_path / "p")
    settings = root / ".factory/settings.json"
    _write(settings, json.dumps({"enabledPlugins": {"x@m": False}}))

    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "droid", "--yes"]) == 0
    )

    fragment = json.loads((root / "loadout/plugins.json").read_text())
    assert fragment["plugins"] == {"x": {"marketplace": "m", "enabled": False}}
    assert json.loads(settings.read_text())["enabledPlugins"] == {"x@m": False}
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_a_codex_plugin_switched_off_is_carried(tmp_path: Path, fake_home: Path) -> None:
    config = fake_home / ".codex/config.toml"
    _write(
        config,
        '[plugins."x@m"]\nenabled = false\n\n[plugins."y@m"]\nenabled = true\n',
    )
    source = _git(tmp_path / "source")

    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0

    fragment = json.loads((source / "loadout/plugins/codex.json").read_text())
    assert fragment["plugins"] == {
        "x": {"marketplace": "m", "enabled": False},
        "y": {"marketplace": "m"},
    }
    assert tomllib.loads(config.read_text())["plugins"] == {"y@m": {"enabled": True}}
    assert loadout.main(["check", "--global"]) == 0


# --- noted: nothing the harness does changes ----------------------------------------


def test_a_codex_approval_mode_codex_rejects(tmp_path: Path, fake_home: Path, capsys) -> None:
    _write(
        fake_home / ".codex/config.toml",
        '[mcp_servers.docs]\ncommand = "docs-mcp"\ndefault_tools_approval_mode = "bogus"\n',
    )

    _converts(*_global(tmp_path / "source", "codex", capsys), "not one Codex accepts")


def test_a_claude_project_server_document_key_claude_ignores(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    _write(root / ".mcp.json", json.dumps({"mcpServers": {}, "extra": 1}))

    _converts(*_project(root, "claude", capsys), "holds extra, which the harness ignores")


def test_a_pi_server_type_alone(tmp_path: Path, fake_home: Path, capsys) -> None:
    mcp = fake_home / ".pi/agent/mcp.json"
    _write(mcp, json.dumps({"mcpServers": {"docs": {"type": "stdio", "command": "docs-mcp"}}}))

    _converts(*_global(tmp_path / "source", "pi", capsys), "docs type is not adopted")


def test_codex_hooks_a_file_codex_rejected(tmp_path: Path, fake_home: Path, capsys) -> None:
    hooks = {"version": 1, "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}}
    _write(fake_home / ".codex/hooks.json", json.dumps(hooks))

    _converts(*_global(tmp_path / "source", "codex", capsys), "Codex does not accept")


def test_a_pi_package_with_no_source(tmp_path: Path, fake_home: Path, capsys) -> None:
    _write(fake_home / ".pi/agent/settings.json", json.dumps({"packages": [{"extensions": []}]}))

    _converts(*_global(tmp_path / "source", "pi", capsys), "Pi 1.4.0 stops at startup")


def test_a_droid_marketplace_no_plugin_uses(tmp_path: Path, fake_home: Path, capsys) -> None:
    market = {"extraKnownMarketplaces": {"tools": {"source": {"source": "github", "repo": "a/b"}}}}
    _write(fake_home / ".factory/settings.json", json.dumps(market))

    _converts(*_global(tmp_path / "source", "droid", capsys), "add it to the plugins fragment")


def test_droid_settings_hooks_beside_the_legacy_hooks_file_stay_put(tmp_path: Path, capsys) -> None:
    root = _git(tmp_path / "p")
    events = {"Stop": [{"hooks": [{"type": "command", "command": "x"}]}]}
    settings = root / ".factory/settings.json"
    _write(settings, json.dumps({"model": "m", "hooks": events}))
    _write(root / ".factory/hooks/hooks.json", json.dumps(events))

    _converts(*_project(root, "droid", capsys), f"{settings}: {DROID_INERT_SETTINGS_HOOKS}")
