from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fixture_root import EXPECTED
from loadout import stealth_git
from loadout.emit import RenderContext, render_project, render_project_detailed
from loadout.machine import config_dir
from loadout.output import Copied, Output
from loadout.permissions.rules import Rules
from loadout.personal import (
    BELOW_TOP_LEVEL,
    NO_PERSONAL,
    PersonalReport,
    PersonalTier,
    adopted_target,
    clash_refusal,
    personal_inputs,
    personal_tier,
)
from loadout.stealth_git import GitFailure
from loadout.stealth_registry import RegisteredStealth, render_registry

PI_PERMISSIONS = ".pi/extensions/pi-permission-system/config.json"
TEAM_SERVER = '[team]\ntransport = "http"\nurl = "https://team.example/mcp"\n'
MINE_SERVER = '[mine]\ntransport = "http"\nurl = "https://mine.example/mcp"\n'


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _project(root: Path, harnesses: str, files: dict[str, str] | None = None) -> Path:
    (root / "loadout").mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "loadout/config.toml").write_text(f"harnesses = [{harnesses}]\n", encoding="utf-8")
    for name, text in (files or {}).items():
        (root / "loadout" / name).write_text(text, encoding="utf-8")
    return root.resolve()


def _stealth(
    home: Path, name: str, target: Path, body: str, files: dict[str, str] | None = None
) -> Path:
    folder = home / "stealth" / name
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(
        f'[stealth]\ntargets = ["{target}"]\n\n[[source]]\nname = "{name}"\npath = "."\n\n' + body,
        encoding="utf-8",
    )
    for relative, text in (files or {}).items():
        (folder / relative).write_text(text, encoding="utf-8")
    return folder.resolve()


def _register(*entries: RegisteredStealth) -> None:
    path = config_dir() / "stealth.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_registry(entries), encoding="utf-8")


def _personal(root: Path) -> RenderContext:
    return RenderContext(personal=personal_tier(root))


def _json(outputs: dict[Path, Output], path: Path) -> Any:
    return json.loads(str(outputs[path]))


def test_a_personal_server_reaches_pi_and_not_cursor_which_the_source_does_not_declare(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"cursor", "pi"', {"mcp.toml": TEAM_SERVER})
    folder = _stealth(fake_home, "mine", root, "[pi]\n\n[codex]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert list(_json(render.outputs, root / ".pi/mcp.json")["mcpServers"]) == ["team", "mine"]
    assert list(_json(render.outputs, root / ".cursor/mcp.json")["mcpServers"]) == ["team"]
    assert list(_json(render.team, root / ".pi/mcp.json")["mcpServers"]) == ["team"]
    assert render.report == PersonalReport(
        notes=(
            "stealth mine: this project does not declare codex, so nothing personal renders for it",
        ),
        contributors=("mine",),
    )


def test_a_personal_server_alone_writes_pis_mcp_file_and_leaves_cursors_unwritten(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"cursor", "pi"')
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert list(_json(render.outputs, root / ".pi/mcp.json")["mcpServers"]) == ["mine"]
    assert root / ".cursor/mcp.json" not in render.outputs
    assert root / ".pi/mcp.json" not in render.team


def test_cursor_mcp_false_keeps_the_personal_server_out_of_cursor_while_pi_gets_it(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"cursor", "pi"', {"mcp.toml": TEAM_SERVER})
    folder = _stealth(
        fake_home, "mine", root, "[cursor]\nmcp = false\n\n[pi]\n", {"mcp.toml": MINE_SERVER}
    )
    _register(RegisteredStealth("mine", folder))

    outputs = render_project(root, _personal(root))

    assert list(_json(outputs, root / ".cursor/mcp.json")["mcpServers"]) == ["team"]
    assert list(_json(outputs, root / ".pi/mcp.json")["mcpServers"]) == ["team", "mine"]


def test_a_personal_cursor_rule_lands_beside_the_teams(fake_home: Path) -> None:
    root = _project(
        fake_home / "work/app",
        '"cursor"',
        {"permissions.toml": '[shell]\nallow = ["git status"]\n'},
    )
    folder = _stealth(
        fake_home, "mine", root, "[cursor]\n", {"permissions.toml": '[shell]\nallow = ["ls"]\n'}
    )
    _register(RegisteredStealth("mine", folder))

    outputs = render_project(root, _personal(root))

    assert _json(outputs, root / ".cursor/cli.json")["permissions"]["allow"] == [
        "Shell(git status)",
        "Shell(ls)",
    ]


def test_a_team_ask_beats_a_personal_allow_which_renders_once_the_team_rule_is_gone(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app",
        '"pi"',
        {"permissions.toml": '[shell]\nask = ["npm publish"]\nallow = ["git status"]\n'},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[pi]\n",
        {"permissions.toml": '[shell]\nallow = ["npm publish"]\n'},
    )
    _register(RegisteredStealth("mine", folder))

    bash = _json(render_project(root, _personal(root)), root / PI_PERMISSIONS)["permission"]["bash"]
    assert bash["npm publish"] == "ask"

    (root / "loadout/permissions.toml").write_text(
        '[shell]\nallow = ["git status"]\n', encoding="utf-8"
    )
    bash = _json(render_project(root, _personal(root)), root / PI_PERMISSIONS)["permission"]["bash"]
    assert bash["npm publish"] == "allow"


def test_a_personal_server_never_lands_in_the_claude_project_mcp_json(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"claude", "pi"', {"mcp.toml": TEAM_SERVER})
    folder = _stealth(fake_home, "mine", root, "[claude]\n\n[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    outputs = render_project(root, _personal(root))

    assert list(_json(outputs, root / ".mcp.json")["mcpServers"]) == ["team"]
    assert list(_json(outputs, root / ".pi/mcp.json")["mcpServers"]) == ["team", "mine"]


def test_a_personal_server_clashing_with_the_teams_is_refused_and_the_teams_renders(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": TEAM_SERVER})
    clashing = '[team]\ntransport = "http"\nurl = "https://other.example/mcp"\n'
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": clashing + MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    servers = _json(render.outputs, root / ".pi/mcp.json")["mcpServers"]
    assert list(servers) == ["team", "mine"]
    assert servers["team"]["url"] == "https://team.example/mcp"
    assert render.report.refusals == (
        clash_refusal("mine", "server", "team", folder / "loadout.toml"),
    )
    assert render.report.refusals[0] == (
        f"refused: stealth mine: its server team clashes with the team's server team, which "
        f'wins; rename yours with [servers.team] name = "<new>" in {folder / "loadout.toml"}'
    )


def test_a_server_two_sources_render_goes_to_the_first(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"')
    first = _stealth(fake_home, "alpha", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    later = '[mine]\ntransport = "http"\nurl = "https://later.example/mcp"\n'
    second = _stealth(fake_home, "beta", root, "[pi]\n", {"mcp.toml": later})
    _register(RegisteredStealth("alpha", first), RegisteredStealth("beta", second))

    render = render_project_detailed(root, _personal(root))

    servers = _json(render.outputs, root / ".pi/mcp.json")["mcpServers"]
    assert list(servers) == ["mine"]
    assert servers["mine"]["url"] == "https://mine.example/mcp"
    assert render.report.refusals == (
        clash_refusal("beta", "server", "mine", second / "loadout.toml", "alpha"),
    )


def test_a_broken_source_manifest_is_unknown_and_the_team_still_renders(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": TEAM_SERVER})
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    _register(RegisteredStealth("mine", folder))

    tier = personal_tier(root)
    render = render_project_detailed(root, RenderContext(personal=tier))

    assert [(s.name, s.health, s.stealth) for s in tier.sources] == [("mine", "unknown", None)]
    assert str(folder / "loadout.toml") in (tier.sources[0].problem or "")
    assert render.report.held == ("mine",)
    assert render.outputs == render_project(root)
    assert list(_json(render.outputs, root / ".pi/mcp.json")["mcpServers"]) == ["team"]


def test_a_source_being_removed_is_unknown(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"')
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder, removing=True))

    assert [(s.name, s.health) for s in personal_tier(root).sources] == [("mine", "unknown")]


def test_an_unreadable_registry_is_one_unknown_source_named_registry(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"')
    path = config_dir() / "stealth.toml"
    path.parent.mkdir(parents=True)
    path.write_text("[stealth.mine\n", encoding="utf-8")

    assert [(s.name, s.health) for s in personal_tier(root).sources] == [("registry", "unknown")]


def test_a_project_below_the_top_level_gets_no_tier_and_a_note(fake_home: Path) -> None:
    top = fake_home / "work/app"
    top.mkdir(parents=True)
    _git(top, "init", "-q", "-b", "main")
    root = top / "sub"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["pi"]\n', encoding="utf-8")
    folder = _stealth(fake_home, "mine", top.resolve(), "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    assert not adopted_target(root)
    assert personal_tier(root) == PersonalTier((), BELOW_TOP_LEVEL)


def test_the_default_context_renders_the_expected_output_with_a_matching_source_registered(
    project: Path, fake_home: Path
) -> None:
    _git(project, "init", "-q", "-b", "main")
    folder = _stealth(
        fake_home,
        "mine",
        project.resolve(),
        "[pi]\n",
        {"permissions.toml": '[shell]\nallow = ["personal-only"]\n'},
    )
    _register(RegisteredStealth("mine", folder))
    assert adopted_target(project)
    assert personal_tier(project).matched[0][0] == "mine"

    render = render_project_detailed(project, RenderContext())

    assert render.team is render.outputs
    assert render.report == PersonalReport()
    for path, content in render.outputs.items():
        actual = content.read_bytes().decode("utf-8") if isinstance(content, Copied) else content
        relative = path.relative_to(project)
        assert actual == (EXPECTED / "project" / relative).read_text(encoding="utf-8"), relative
    assert RenderContext().personal is NO_PERSONAL
    personal = render_project(project, _personal(project))
    assert "personal-only" in _json(personal, project / PI_PERMISSIONS)["permission"]["bash"]


def test_a_personal_rule_the_renderer_refuses_is_dropped_and_the_team_still_renders(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app",
        '"pi", "codex"',
        {"permissions.toml": '[mcp]\nallow = ["my-svc/*"]\n'},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[pi]\n[codex]\n",
        {"permissions.toml": '[shell]\nallow = ["ls"]\n[mcp]\nallow = ["my_svc/x"]\n'},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert render.outputs[root / PI_PERMISSIONS] == render_project(root)[root / PI_PERMISSIONS]
    assert render.report.refusals[0].startswith("refused: stealth mine: pi: ")
    assert render.report.refusals[0].endswith("; none of its rules or servers render for pi")
    assert 'prefix_rule(pattern = ["ls"]' in str(
        render.outputs[root / ".codex/rules/permissions.rules"]
    )
    assert render.report.contributors == ("mine",)


def test_a_personal_opencode_extra_cannot_widen_the_teams_verdict(fake_home: Path) -> None:
    root = _project(
        fake_home / "work/app",
        '"opencode"',
        {"permissions.toml": '[opencode.extra]\nwebfetch = "deny"\nbash_note = "ask"\n'},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[opencode]\n",
        {"permissions.toml": '[opencode.extra]\nwebfetch = "allow"\nbash_note = "deny"\n'},
    )
    _register(RegisteredStealth("mine", folder))

    permission = _json(render_project(root, _personal(root)), root / "opencode.json")["permission"]

    assert (permission["webfetch"], permission["bash_note"]) == ("deny", "deny")


def test_a_git_failure_leaves_an_unknown_source_rather_than_an_empty_tier(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(fake_home / "work/app", '"pi"')
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    def failing(_: Path) -> None:
        raise GitFailure("index.lock exists")

    monkeypatch.setattr(stealth_git, "identity", failing)

    tier = personal_tier(root)

    assert [(s.name, s.health, s.problem) for s in tier.sources] == [
        ("checkout", "unknown", "index.lock exists")
    ]


def test_a_git_failure_with_no_source_targeting_the_project_is_an_empty_tier(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(fake_home / "work/app", '"pi"')
    other = fake_home / "work/other"
    other.mkdir(parents=True)
    folder = _stealth(fake_home, "mine", other.resolve(), "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    def failing(_: Path) -> None:
        raise GitFailure("index.lock exists")

    monkeypatch.setattr(stealth_git, "identity", failing)

    assert personal_tier(root) == NO_PERSONAL


def test_a_project_at_the_home_folder_is_an_empty_tier_though_a_source_targets_it(
    fake_home: Path,
) -> None:
    root = _project(fake_home, '"pi"')
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    assert not adopted_target(root)
    assert personal_tier(root) == NO_PERSONAL


def test_personal_inputs_name_the_servers_each_source_added(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": TEAM_SERVER})
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": MINE_SERVER})
    _register(RegisteredStealth("mine", folder))

    tier = personal_tier(root)
    inputs = personal_inputs(tier, ("pi",), Rules(), {})

    assert {name: source for name, (source, _) in inputs.added["pi"].items()} == {"mine": "mine"}
