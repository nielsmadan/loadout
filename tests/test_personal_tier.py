from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from fixture_root import EXPECTED, FIXTURES, build_project_root
from loadout import stealth_git
from loadout.composition import HEADER
from loadout.emit import RenderContext, render_project, render_project_detailed
from loadout.files import applied_document
from loadout.machine import config_dir
from loadout.output import Copied, Merged, Output
from loadout.permissions.rules import Rules
from loadout.personal import (
    BELOW_TOP_LEVEL,
    NO_PERSONAL,
    PersonalReport,
    PersonalTier,
    adopted_claude_servers,
    adopted_target,
    clash_refusal,
    personal_inputs,
    personal_tier,
)
from loadout.servers import parse_servers
from loadout.stealth_git import GitFailure
from loadout.stealth_registry import RegisteredStealth, render_registry
from loadout.written import merged_entry, record_written, text_entry

PERSONAL_FIXTURES = FIXTURES / "personal"
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
        (root / "loadout" / name).parent.mkdir(parents=True, exist_ok=True)
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
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
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


ALL_HARNESSES = '"claude", "codex", "cursor", "droid", "opencode", "pi"'
SKILL = "---\nname: {name}\ndescription: d\n---\n{body}\n"


def _every_harness(instructions: str = 'instructions = ["mine"]\n') -> str:
    return "".join(
        f"[{harness}]\n{instructions}"
        + ('settings = "local"\n' if harness in ("claude", "droid") else "")
        + "\n"
        for harness in ("claude", "codex", "cursor", "droid", "opencode", "pi")
    )


def _apply(path: Path, output: Output) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = applied_document(path, output) if isinstance(output, Merged) else str(output)
    path.write_text(text, encoding="utf-8")


def _rendered_skill(outputs: dict[Path, Output], path: Path) -> str:
    output = outputs[path]
    return output.read_bytes().decode() if isinstance(output, Copied) else str(output)


def test_every_personal_only_destination_renders_and_the_team_render_has_none(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", ALL_HARNESSES)
    folder = _stealth(
        fake_home,
        "mine",
        root,
        _every_harness(),
        {
            "instructions/mine.md": "Mine.\n",
            "settings/local.json": '{"model": "opus"}',
            "mcp.toml": MINE_SERVER,
            "skills/probe/SKILL.md": SKILL.format(name="probe", body="Personal"),
        },
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))
    outputs = render.outputs

    assert outputs[root / "CLAUDE.local.md"] == f"{HEADER}\n\nMine.\n"
    assert outputs[root / ".factory/AGENTS.md"] == f"{HEADER}\n\nMine.\n"
    assert outputs[root / ".pi/APPEND_SYSTEM.md"] == f"{HEADER}\n\nMine.\n"
    assert "Mine." in str(outputs[root / ".cursor/rules/mine.mdc"])
    codex = outputs[root / ".codex/config.toml"]
    assert isinstance(codex, Merged)
    assert codex.owned == {"developer_instructions", "mcp_servers.mine"}
    assert tomllib.loads(codex.document)["developer_instructions"] == "Mine."
    assert list(tomllib.loads(codex.document)["mcp_servers"]) == ["mine"]
    assert _json(outputs, root / "opencode.json")["instructions"] == [
        str(folder / ".loadout/opencode-instructions.md")
    ]
    for settings in (".claude/settings.local.json", ".factory/settings.local.json"):
        merged = outputs[root / settings]
        assert isinstance(merged, Merged)
        assert (merged.owned, json.loads(merged.document)) == ({"model"}, {"model": "opus"})
    assert _rendered_skill(outputs, root / ".claude/skills/probe/SKILL.md").endswith("Personal\n")
    assert render.report.contributors == ("mine",)
    personal_only = {
        "CLAUDE.local.md",
        ".factory/AGENTS.md",
        ".pi/APPEND_SYSTEM.md",
        ".cursor/rules/mine.mdc",
        ".codex/config.toml",
        ".claude/settings.local.json",
        ".factory/settings.local.json",
        ".claude/skills/probe/SKILL.md",
        ".factory/skills/probe/SKILL.md",
        ".agents/skills/probe/SKILL.md",
        ".cursor/mcp.json",
        ".factory/mcp.json",
        ".pi/mcp.json",
    }
    assert {str(path.relative_to(root)) for path in set(outputs) - set(render.team)} == (
        personal_only
    )
    assert "instructions" not in _json(render.team, root / "opencode.json")


def test_agents_md_and_claude_md_are_the_same_bytes_with_and_without_the_tier(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app", '"claude", "codex"', {"instructions/base.md": "Base.\n"}
    )
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude", "codex"]\ninstructions = ["base"]\n', encoding="utf-8"
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\ninstructions = ["mine"]\n\n[codex]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "Mine.\n"},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    for name in ("AGENTS.md", "CLAUDE.md"):
        assert render.outputs[root / name] == render.team[root / name]
        assert render.outputs[root / name] == render_project(root)[root / name]
    assert "Base." in str(render.outputs[root / "AGENTS.md"])
    assert root / "CLAUDE.local.md" in render.outputs


def test_a_team_skill_beats_a_personal_one_of_the_same_name(fake_home: Path) -> None:
    root = _project(
        fake_home / "work/app",
        '"claude"',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Team")},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[claude]\n",
        {
            "skills/foo/SKILL.md": SKILL.format(name="foo", body="Personal"),
            "skills/bar/SKILL.md": SKILL.format(name="bar", body="Mine"),
        },
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert _rendered_skill(render.outputs, root / ".claude/skills/foo/SKILL.md").endswith("Team\n")
    assert _rendered_skill(render.outputs, root / ".claude/skills/bar/SKILL.md").endswith("Mine\n")
    assert render.report.refusals == (
        (
            f"refused: stealth mine: its skill foo clashes with the team's skill foo, which wins; "
            f'rename yours with [skills.foo] name = "<new>" in {folder / "loadout.toml"} or name '
            "its folder foo.local to replace the team's"
        ),
    )


def test_a_personal_skill_aliased_away_from_the_teams_name_renders_beside_it(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app",
        '"claude"',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Team")},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\n\n[skills.foo]\nname = "foo-mine"\n',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Personal")},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert _rendered_skill(render.outputs, root / ".claude/skills/foo/SKILL.md").endswith("Team\n")
    mine = _rendered_skill(render.outputs, root / ".claude/skills/foo-mine/SKILL.md")
    assert "name: foo-mine\n" in mine
    assert mine.endswith("Personal\n")
    assert render.report.refusals == ()


def test_a_local_skill_replaces_the_teams_only_for_the_harnesses_the_source_selects(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app",
        '"claude", "pi"',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Team")},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[claude]\n",
        {"skills/foo.local/SKILL.md": SKILL.format(name="foo", body="Personal")},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    claude = _rendered_skill(render.outputs, root / ".claude/skills/foo/SKILL.md")
    assert claude.endswith("Personal\n")
    assert _rendered_skill(render.outputs, root / ".pi/skills/foo/SKILL.md").endswith("Team\n")
    assert _rendered_skill(render.team, root / ".claude/skills/foo/SKILL.md").endswith("Team\n")
    assert render.report.refusals == ()


def test_two_sources_with_instructions_give_one_claude_local_md_in_registry_order(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    second = _stealth(
        fake_home,
        "beta",
        root,
        '[claude]\ninstructions = ["b"]\n',
        {"instructions/b.md": "Second.\n"},
    )
    first = _stealth(
        fake_home,
        "alpha",
        root,
        '[claude]\ninstructions = ["a"]\n',
        {"instructions/a.md": "First.\n"},
    )
    _register(RegisteredStealth("beta", second), RegisteredStealth("alpha", first))

    render = render_project_detailed(root, _personal(root))

    assert [source.name for source in personal_tier(root).sources] == ["alpha", "beta"]
    assert render.outputs[root / "CLAUDE.local.md"] == f"{HEADER}\n\nFirst.\n\nSecond.\n"
    assert render.report.contributors == ("alpha", "beta")


def test_a_server_two_sources_render_for_codex_and_claude_goes_to_the_first(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude", "codex"', {"mcp.toml": TEAM_SERVER})
    first = _stealth(fake_home, "alpha", root, "[claude]\n[codex]\n", {"mcp.toml": MINE_SERVER})
    later = '[mine]\ntransport = "http"\nurl = "https://later.example/mcp"\n'
    second = _stealth(fake_home, "beta", root, "[claude]\n[codex]\n", {"mcp.toml": later})
    _register(RegisteredStealth("alpha", first), RegisteredStealth("beta", second))
    tier = personal_tier(root)

    render = render_project_detailed(root, RenderContext(personal=tier))

    codex = render.outputs[root / ".codex/config.toml"]
    assert isinstance(codex, Merged)
    servers = tomllib.loads(codex.document)["mcp_servers"]
    assert servers == {"mine": {"url": "https://mine.example/mcp"}}
    assert render.report.refusals == (
        clash_refusal("beta", "server", "mine", second / "loadout.toml", "alpha"),
    )
    assert render.report.refusals[0].endswith(
        f'wins; rename yours with [servers.mine] name = "<new>" in {second / "loadout.toml"}'
    )
    alpha, beta = tier.sources
    assert list(adopted_claude_servers(root, alpha, parse_servers(root / "loadout/mcp.toml"))) == [
        "mine"
    ]
    assert adopted_claude_servers(root, beta, parse_servers(root / "loadout/mcp.toml")) == {}


def test_a_claude_server_the_team_also_defines_stays_out_of_the_leaves(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"claude"', {"mcp.toml": TEAM_SERVER})
    clashing = '[team]\ntransport = "http"\nurl = "https://other.example/mcp"\n'
    folder = _stealth(fake_home, "mine", root, "[claude]\n", {"mcp.toml": clashing + MINE_SERVER})
    _register(RegisteredStealth("mine", folder))
    tier = personal_tier(root)

    render = render_project_detailed(root, RenderContext(personal=tier))

    assert list(
        adopted_claude_servers(root, tier.sources[0], parse_servers(root / "loadout/mcp.toml"))
    ) == ["mine"]
    assert list(adopted_claude_servers(root, tier.sources[0], {})) == ["team", "mine"]
    assert render.report.refusals == (
        clash_refusal("mine", "server", "team", folder / "loadout.toml"),
    )


def test_an_aliased_servers_clash_refusal_names_its_original_table(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": TEAM_SERVER})
    original = '[orig]\ntransport = "http"\nurl = "https://orig.example/mcp"\n'
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[pi]\n\n[servers.orig]\nname = "team"\n',
        {"mcp.toml": original},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    assert render.report.refusals == (
        (
            f"refused: stealth mine: its server team clashes with the team's server team, which "
            f'wins; rename yours with [servers.orig] name = "<new>" in {folder / "loadout.toml"}'
        ),
    )
    assert list(_json(render.outputs, root / ".pi/mcp.json")["mcpServers"]) == ["team"]


def test_a_settings_key_two_sources_set_goes_to_the_first(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    first = _stealth(
        fake_home,
        "alpha",
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"model": "opus"}'},
    )
    second = _stealth(
        fake_home,
        "beta",
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"model": "haiku", "theme": "dark"}'},
    )
    _register(RegisteredStealth("alpha", first), RegisteredStealth("beta", second))

    render = render_project_detailed(root, _personal(root))

    merged = render.outputs[root / ".claude/settings.local.json"]
    assert isinstance(merged, Merged)
    assert json.loads(merged.document) == {"model": "opus", "theme": "dark"}
    assert render.report.refusals == (
        (
            "refused: stealth beta: its claude setting model clashes with stealth alpha's claude "
            "setting model, which wins; remove yours from the claude settings fragment in "
            f"{second / 'loadout.toml'}"
        ),
    )


def test_a_key_dropped_from_the_settings_fragment_is_stripped_and_a_user_key_survives(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"model": "opus", "theme": "dark"}'},
    )
    _register(RegisteredStealth("mine", folder))
    path = root / ".claude/settings.local.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"user": true}\n', encoding="utf-8")

    first = render_project(root, _personal(root))[path]
    assert isinstance(first, Merged)
    _apply(path, first)
    record_written(root, {path: merged_entry(first)})
    assert json.loads(path.read_text()) == {"user": True, "model": "opus", "theme": "dark"}

    (folder / "settings/local.json").write_text('{"model": "opus"}', encoding="utf-8")
    second = render_project(root, _personal(root))[path]
    assert isinstance(second, Merged)
    _apply(path, second)

    assert second.owned == {"model", "theme"}
    assert json.loads(path.read_text()) == {"user": True, "model": "opus"}


def test_dropping_personal_instructions_drops_the_opencode_instructions_key(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app", '"opencode"', {"permissions.toml": '[shell]\nallow = ["ls"]\n'}
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[opencode]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "Mine.\n"},
    )
    _register(RegisteredStealth("mine", folder))
    path = root / "opencode.json"

    _apply(path, render_project(root, _personal(root))[path])
    on_disk = json.loads(path.read_text())
    assert on_disk["instructions"] == [str(folder / ".loadout/opencode-instructions.md")]
    path.write_text(
        json.dumps({**on_disk, "instructions": ["notes.md", *on_disk["instructions"]]}),
        encoding="utf-8",
    )

    (folder / "loadout.toml").write_text(
        f'[stealth]\ntargets = ["{root}"]\n\n[[source]]\nname = "mine"\npath = "."\n\n[opencode]\n',
        encoding="utf-8",
    )
    rendered = _json(render_project(root, _personal(root)), path)

    assert rendered["instructions"] == ["notes.md"]
    assert rendered["permission"]["bash"]["ls"] == "allow"
    path.write_text(json.dumps({**rendered, "instructions": []}), encoding="utf-8")
    assert "instructions" not in _json(render_project(root, _personal(root)), path)


def test_claude_local_md_is_the_same_on_two_consecutive_renders(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"claude"', {"instructions/base.md": "B.\n"})
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude"]\ninstructions = ["base"]\n', encoding="utf-8"
    )
    (root / "AGENTS.md").write_text("Hand.\n", encoding="utf-8")
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "Mine.\n"},
    )
    _register(RegisteredStealth("mine", folder))

    first = render_project(root, _personal(root))
    assert not (root / "CLAUDE.md").exists()
    for path, output in first.items():
        if isinstance(output, str):
            _apply(path, output)
    second = render_project(root, _personal(root))

    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == first[root / "CLAUDE.md"]
    assert first[root / "CLAUDE.local.md"] == f"{HEADER}\n\nMine.\n"
    assert second[root / "CLAUDE.local.md"] == first[root / "CLAUDE.local.md"]


def test_a_hand_written_agents_md_the_team_does_not_render_is_imported_on_every_render(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    (root / "AGENTS.md").write_text("Hand.\n", encoding="utf-8")
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "Mine.\n"},
    )
    _register(RegisteredStealth("mine", folder))

    first = render_project(root, _personal(root))[root / "CLAUDE.local.md"]
    second = render_project(root, _personal(root))[root / "CLAUDE.local.md"]

    assert first == second == f"{HEADER}\n\n@AGENTS.md\n\nMine.\n"


def test_a_recorded_agents_md_the_team_stopped_rendering_is_not_imported(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    agents = root / "AGENTS.md"
    agents.write_text("Old.\n", encoding="utf-8")
    record_written(root, {agents: text_entry(agents, "Old.\n")})
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[claude]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "Mine.\n"},
    )
    _register(RegisteredStealth("mine", folder))

    before_retirement = render_project(root, _personal(root))[root / "CLAUDE.local.md"]
    agents.unlink()
    after_retirement = render_project(root, _personal(root))[root / "CLAUDE.local.md"]

    assert before_retirement == after_retirement == f"{HEADER}\n\nMine.\n"


def test_a_personal_skill_that_fails_to_render_refuses_its_source_and_the_team_renders(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app",
        '"claude", "pi"',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Team")},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[claude]\n",
        {
            "skills/bad/SKILL.md": SKILL.format(name="bad", body="::: claude\nunclosed"),
            "skills/good/SKILL.md": SKILL.format(name="good", body="Mine"),
        },
    )
    _register(RegisteredStealth("mine", folder))
    tier = personal_tier(root)

    render = render_project_detailed(root, RenderContext(personal=tier))

    assert [(s.name, s.health) for s in tier.sources] == [("mine", "matched")]
    assert render.outputs == render.team
    assert _rendered_skill(render.outputs, root / ".claude/skills/foo/SKILL.md").endswith("Team\n")
    assert render.report.refusals == (
        (
            "refused: stealth mine: bad/SKILL.md: a ::: section was opened and never closed; "
            "none of its skills render"
        ),
    )


@pytest.mark.parametrize(
    ("selected", "other"), [("pi", "codex"), ("codex", "pi")], ids=["pi-selected", "codex-selected"]
)
def test_a_local_skill_replaces_the_teams_in_the_shared_skill_folder_and_notes_who_else_reads_it(
    fake_home: Path, selected: str, other: str
) -> None:
    root = _project(
        fake_home / "work/app",
        '"codex", "pi"',
        {"skills/foo/SKILL.md": SKILL.format(name="foo", body="Team")},
    )
    folder = _stealth(
        fake_home,
        "mine",
        root,
        f"[{selected}]\n",
        {"skills/foo.local/SKILL.md": SKILL.format(name="foo", body="Personal")},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, _personal(root))

    def copies(outputs: dict[Path, Output]) -> dict[str, str]:
        return {
            str(path.relative_to(root)): _rendered_skill(outputs, path).rsplit("\n", 2)[-2]
            for path in outputs
            if path.name == "SKILL.md" and path.parent.name == "foo"
        }

    assert copies(render.team) == {".agents/skills/foo/SKILL.md": "Team"}
    assert copies(render.outputs) == {".agents/skills/foo/SKILL.md": "Personal"}
    assert render.report.notes == (
        (
            "stealth mine: its skill foo replaces the team's in .agents/skills, so the personal "
            f"copy also reaches {other}"
        ),
    )
    assert render.report.refusals == ()


def _break(folder: Path) -> None:
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")


def test_a_held_sources_opencode_instructions_entry_stays_while_another_source_renders(
    fake_home: Path,
) -> None:
    root = _project(
        fake_home / "work/app", '"opencode"', {"permissions.toml": '[shell]\nallow = ["ls"]\n'}
    )
    sources = {
        name: _stealth(
            fake_home,
            name,
            root,
            f'[opencode]\ninstructions = ["{name}"]\n',
            {f"instructions/{name}.md": f"{name}.\n"},
        )
        for name in ("alpha", "beta")
    }
    _register(*(RegisteredStealth(name, folder) for name, folder in sources.items()))
    path = root / "opencode.json"
    entries = [str(folder / ".loadout/opencode-instructions.md") for folder in sources.values()]
    _apply(path, render_project(root, _personal(root))[path])
    assert json.loads(path.read_text())["instructions"] == entries

    _break(sources["alpha"])
    render = render_project_detailed(root, _personal(root))

    assert render.report.held == ("alpha",)
    assert _json(render.outputs, path)["instructions"] == entries


def test_a_held_sources_codex_server_stays_while_another_source_renders(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"codex"')
    other = '[other]\ntransport = "http"\nurl = "https://other.example/mcp"\n'
    alpha = _stealth(fake_home, "alpha", root, "[codex]\n", {"mcp.toml": MINE_SERVER})
    beta = _stealth(fake_home, "beta", root, "[codex]\n", {"mcp.toml": other})
    _register(RegisteredStealth("alpha", alpha), RegisteredStealth("beta", beta))
    path = root / ".codex/config.toml"
    first = render_project(root, _personal(root))[path]
    assert isinstance(first, Merged)
    _apply(path, first)
    record_written(root, {path: merged_entry(first)})
    assert list(tomllib.loads(path.read_text())["mcp_servers"]) == ["mine", "other"]

    _break(alpha)
    render = render_project_detailed(root, _personal(root))
    second = render.outputs[path]
    assert isinstance(second, Merged)
    _apply(path, second)

    assert render.report.held == ("alpha",)
    assert tomllib.loads(path.read_text())["mcp_servers"] == {
        "mine": {"url": "https://mine.example/mcp"},
        "other": {"url": "https://other.example/mcp"},
    }


def test_a_held_sources_settings_key_stays_while_another_source_renders(
    fake_home: Path,
) -> None:
    root = _project(fake_home / "work/app", '"claude"')
    alpha = _stealth(
        fake_home,
        "alpha",
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"model": "opus"}'},
    )
    beta = _stealth(
        fake_home,
        "beta",
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"theme": "dark"}'},
    )
    _register(RegisteredStealth("alpha", alpha), RegisteredStealth("beta", beta))
    path = root / ".claude/settings.local.json"
    first = render_project(root, _personal(root))[path]
    assert isinstance(first, Merged)
    _apply(path, first)
    record_written(root, {path: merged_entry(first)})
    (beta / "settings/local.json").write_text(
        '{"theme": "light", "verbose": true}', encoding="utf-8"
    )

    _break(alpha)
    render = render_project_detailed(root, _personal(root))
    second = render.outputs[path]
    assert isinstance(second, Merged)
    _apply(path, second)

    assert render.report.held == ("alpha",)
    assert second.owned == {"verbose"}
    assert json.loads(path.read_text()) == {"model": "opus", "theme": "dark", "verbose": True}


def test_a_root_with_a_line_break_is_not_an_adopted_target(fake_home: Path) -> None:
    root = fake_home / "we\nird"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["pi"]\n', encoding="utf-8")

    assert not adopted_target(root)


def test_the_personal_fixture_denies_what_the_project_fixture_allows_through_the_real_tier(
    tmp_path: Path, fake_home: Path
) -> None:
    root = build_project_root(tmp_path / "adopted")
    _git(root, "init", "-q", "-b", "main")
    root = root.resolve()
    folder = _stealth(
        fake_home,
        "mine",
        root,
        "[pi]\n",
        {"permissions.toml": (PERSONAL_FIXTURES / "permissions.toml").read_text(encoding="utf-8")},
    )
    _register(RegisteredStealth("mine", folder))

    team = _json(render_project(root, RenderContext()), root / PI_PERMISSIONS)["permission"]["bash"]
    merged = _json(render_project(root, _personal(root)), root / PI_PERMISSIONS)["permission"][
        "bash"
    ]

    assert team["zeta"] == "allow"
    assert "kappa only-here" not in team
    assert merged["zeta"] == "deny"
    assert merged["kappa only-here"] == "allow"
