from __future__ import annotations

import fnmatch
import json
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

from loadout.composition import HEADER
from loadout.emit import render_project
from loadout.errors import LoadoutError
from loadout.output import Copied, Output
from loadout.overlay_documents import OwnedJson
from loadout.overlay_manifest import load_overlay_manifest
from loadout.overlay_render import (
    OverlayOutput,
    TargetFacts,
    gather_facts,
    render_overlay,
)
from loadout.overlay_renderers import (
    claude_owned,
    droid_owned,
    opencode_owned,
    render_codex_overlay_config,
    render_opencode_overlay_permission,
)
from loadout.overlay_seams import codex_rules
from loadout.permissions.renderers import CODEX_OVERLAY_HEADER
from loadout.permissions.rules import Rules
from loadout.servers import parse_servers

FIXTURES = Path(__file__).parent / "fixtures"
NO_FACTS = TargetFacts(agents_md=False, claude_md_on_path=False)


def make_overlay(folder: Path, body: str, files: dict[str, str] | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "loadout.toml").write_text(
        '[overlay]\ntargets = ["~/app*"]\n\n[[source]]\nname = "app"\npath = "."\n' + body,
        encoding="utf-8",
    )
    for name, text in (files or {}).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def render(folder: Path, target: Path, facts: TargetFacts = NO_FACTS) -> dict[Path, OverlayOutput]:
    return render_overlay(load_overlay_manifest(folder), "app", target, facts).files


def relative(files: dict[Path, Any], target: Path) -> set[str]:
    return {str(path.relative_to(target)) for path in files}


def content(output: Output | OwnedJson) -> str | bytes:
    assert isinstance(output, (str, Copied))
    return output.read_bytes() if isinstance(output, Copied) else output


def test_agents_md_with_no_claude_md_on_the_path_is_imported(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        '\n[claude]\ninstructions = ["one", "two"]\n',
        {"instructions/one.md": "First.\n", "instructions/two.md": "Second.\n"},
    )
    target = tmp_path / "t"
    target.mkdir()
    facts = TargetFacts(agents_md=True, claude_md_on_path=False)
    assert render(folder, target, facts)[target / "CLAUDE.local.md"] == (
        f"{HEADER}\n\n@AGENTS.md\n\nFirst.\n\nSecond.\n"
    )


def test_a_claude_md_on_the_path_means_no_import(tmp_path: Path) -> None:
    # Claude Code memory docs: a project CLAUDE.md may live at ./CLAUDE.md or ./.claude/CLAUDE.md
    folder = make_overlay(
        tmp_path / "ov", '\n[claude]\ninstructions = ["one"]\n', {"instructions/one.md": "First.\n"}
    )
    expected = f"{HEADER}\n\nFirst.\n"
    for index, marker in enumerate(
        ["parent/CLAUDE.md", "parent/root/.claude/CLAUDE.md", "parent/CLAUDE.local.md"]
    ):
        scope = tmp_path / f"case{index}"
        target = scope / "parent" / "root"
        target.mkdir(parents=True)
        (scope / marker).parent.mkdir(parents=True, exist_ok=True)
        (scope / marker).write_text("x", encoding="utf-8")
        facts = gather_facts(target)
        assert facts == TargetFacts(agents_md=False, claude_md_on_path=True)
        assert (
            render(folder, target, TargetFacts(agents_md=True, claude_md_on_path=True))[
                target / "CLAUDE.local.md"
            ]
            == expected
        )


def test_the_user_level_memory_file_is_not_a_claude_md_on_the_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/CLAUDE.md").write_text("user", encoding="utf-8")
    target = home / "work" / "app"
    target.mkdir(parents=True)
    (target / "AGENTS.md").write_text("team", encoding="utf-8")
    facts = gather_facts(target, {"HOME": str(home)})
    assert facts == TargetFacts(agents_md=True, claude_md_on_path=False)
    folder = make_overlay(
        tmp_path / "ov", '\n[claude]\ninstructions = ["one"]\n', {"instructions/one.md": "First.\n"}
    )
    assert render(folder, target, facts)[target / "CLAUDE.local.md"] == (
        f"{HEADER}\n\n@AGENTS.md\n\nFirst.\n"
    )


def test_a_claude_config_dir_memory_file_is_the_user_level_one(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = tmp_path / "cfg"
    config.mkdir()
    (config / "CLAUDE.md").write_text("user", encoding="utf-8")
    (home / ".claude").mkdir(parents=True)
    (home / ".claude/CLAUDE.md").write_text("not the user file now", encoding="utf-8")
    target = home / "app"
    target.mkdir()
    environ = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(config)}
    assert gather_facts(target, environ).claude_md_on_path is True
    (home / ".claude/CLAUDE.md").unlink()
    assert gather_facts(target, environ).claude_md_on_path is False


def test_a_dot_claude_claude_md_in_a_non_root_ancestor_counts(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / "work/.claude").mkdir(parents=True)
    (home / "work/.claude/CLAUDE.md").write_text("x", encoding="utf-8")
    target = home / "work" / "app"
    target.mkdir()
    (target / "AGENTS.md").write_text("team", encoding="utf-8")
    assert gather_facts(target, {"HOME": str(home)}) == TargetFacts(
        agents_md=True, claude_md_on_path=True
    )


def test_a_relative_root_is_resolved_before_its_ancestors_are_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path)
    assert gather_facts(Path("proj"), {"HOME": str(tmp_path / "elsewhere")}).claude_md_on_path


def test_agents_md_without_any_claude_md_is_found_and_absent_agents_md_is_not(
    tmp_path: Path,
) -> None:
    target = tmp_path / "t"
    target.mkdir()
    env = {"HOME": str(tmp_path / "h")}
    assert gather_facts(target, env) == TargetFacts(agents_md=False, claude_md_on_path=False)


def test_a_non_object_permissions_value_in_a_claude_fragment_is_refused(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        '\n[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"permissions": "oops"}'},
    )
    with pytest.raises(LoadoutError, match=r"local\.json: .*permissions must be a JSON object"):
        render(folder, tmp_path / "t")


def test_the_overlays_own_claude_local_md_is_not_a_claude_md_on_the_path(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.local.md").write_text("mine", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("team", encoding="utf-8")
    assert gather_facts(tmp_path) == TargetFacts(agents_md=True, claude_md_on_path=False)


def test_claude_owned_lists_the_fragment_keys_then_the_rule_lists() -> None:
    rules = Rules(allow=("ls",), deny=("rm",), ask=("git push",))
    owned = claude_owned(
        rules, {"model": "x", "permissions": {"defaultMode": "plan", "allow": ["ignored"]}}
    )
    assert owned.keys == ((("model",), "x"), (("permissions", "defaultMode"), "plan"))
    assert owned.rules == (
        (("permissions", "allow"), ("Bash(ls:*)",)),
        (("permissions", "ask"), ("Bash(git push:*)",)),
        (("permissions", "deny"), ("Bash(rm:*)",)),
    )


def test_without_permissions_or_settings_claude_writes_no_settings_document(
    tmp_path: Path,
) -> None:
    folder = make_overlay(
        tmp_path / "ov", "\n[claude]\ninstructions = ['one']\n", {"instructions/one.md": "x\n"}
    )
    target = tmp_path / "t"
    assert relative(render(folder, target), target) == {"CLAUDE.local.md"}


def test_claude_settings_render_with_rules_or_a_named_fragment(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        '\n[claude]\nsettings = "local"\n',
        {
            "settings/local.json": '{"model": "opus"}',
            "permissions.toml": '[shell]\nallow = ["ls"]\n',
        },
    )
    target = tmp_path / "t"
    output = render(folder, target)[target / ".claude/settings.local.json"]
    assert output == OwnedJson(
        keys=((("model",), "opus"),),
        rules=((("permissions", "allow"), ("Bash(ls:*)",)),),
    )


def test_droid_owns_the_command_lists_and_the_fragment_keys() -> None:
    owned = droid_owned(Rules(allow=("ls",), ask=("git push",), deny=("rm",)), {"model": "m"})
    assert owned.keys == ((("model",), "m"),)
    assert [path for path, _ in owned.rules] == [
        ("commandAllowlist",),
        ("commandDenylist",),
        ("commandBlocklist",),
    ]
    assert owned.rules[0][1] == ("ls",)


def test_droid_writes_instructions_servers_and_settings(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        '\n[droid]\ninstructions = ["one"]\nsettings = "local"\n',
        {
            "instructions/one.md": "First.\n",
            "settings/local.json": '{"model": "m"}',
            "permissions.toml": '[shell]\ndeny = ["rm"]\n',
            "mcp.toml": '[svc]\ntransport = "http"\nurl = "https://svc.example/mcp"\n',
        },
    )
    target = tmp_path / "t"
    files = render(folder, target)
    assert relative(files, target) == {
        ".factory/AGENTS.md",
        ".factory/mcp.json",
        ".factory/settings.local.json",
    }
    assert files[target / ".factory/AGENTS.md"] == f"{HEADER}\n\nFirst.\n"
    assert json.loads(str(files[target / ".factory/mcp.json"])) == {
        "mcpServers": {"svc": {"type": "http", "url": "https://svc.example/mcp"}}
    }
    assert files[target / ".factory/settings.local.json"] == OwnedJson(
        keys=((("model",), "m"),), rules=((("commandBlocklist",), ("rm",)),)
    )


def test_pi_writes_its_documents_and_refuses_an_overlapping_namespace(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[pi]\ninstructions = ['one']\n",
        {
            "instructions/one.md": "First.\n",
            "permissions.toml": '[shell]\nallow = ["ls"]\n',
            "mcp.toml": '[svc]\ntransport = "http"\nurl = "https://svc.example/mcp"\n',
        },
    )
    target = tmp_path / "t"
    files = render(folder, target)
    assert relative(files, target) == {
        ".pi/APPEND_SYSTEM.md",
        ".pi/mcp.json",
        ".pi/extensions/pi-permission-system/config.json",
    }
    assert json.loads(str(files[target / ".pi/extensions/pi-permission-system/config.json"])) == {
        "permission": {"bash": {"ls": "allow", "ls *": "allow"}}
    }

    (folder / "mcp.toml").write_text(
        '[a__b]\ntransport = "http"\nurl = "https://b/mcp"\n', encoding="utf-8"
    )
    (folder / "permissions.toml").write_text('[mcp]\nallow = ["a/*"]\n', encoding="utf-8")
    with pytest.raises(LoadoutError, match="also matches tools of server 'a__b'"):
        render(folder, target)

    project = tmp_path / "project"
    (project / "loadout").mkdir(parents=True)
    (project / "loadout/config.toml").write_text('harnesses = ["pi"]\n', encoding="utf-8")
    for name in ("permissions.toml", "mcp.toml"):
        shutil.copy(folder / name, project / "loadout" / name)
    with pytest.raises(LoadoutError, match="also matches tools of server 'a__b'"):
        render_project(project)


def test_pi_writes_no_permission_document_when_it_would_hold_no_rule(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov", "\n[pi]\ninstructions = ['one']\n", {"instructions/one.md": "x\n"}
    )
    target = tmp_path / "t"
    assert relative(render(folder, target), target) == {".pi/APPEND_SYSTEM.md"}


def skill_overlay(tmp_path: Path, body: str) -> Path:
    return make_overlay(
        tmp_path / "ov",
        body,
        {
            "skills/probe/SKILL.md": "---\nname: probe\ndescription: d\n---\nBody\n",
            "skills/probe/reference.md": "ref\n",
        },
    )


def test_a_skill_identical_for_codex_opencode_and_pi_lands_once(tmp_path: Path) -> None:
    folder = skill_overlay(tmp_path, "\n[codex]\n[opencode]\n[pi]\n")
    target = tmp_path / "t"
    files = render(folder, target)
    assert relative(files, target) == {
        ".agents/skills/probe/SKILL.md",
        ".agents/skills/probe/reference.md",
    }


def test_a_claude_skill_lands_with_its_supporting_file_copied(tmp_path: Path) -> None:
    folder = skill_overlay(tmp_path, "\n[claude]\n")
    target = tmp_path / "t"
    files = render(folder, target)
    assert relative(files, target) == {
        ".claude/skills/probe/SKILL.md",
        ".claude/skills/probe/reference.md",
    }
    assert files[target / ".claude/skills/probe/reference.md"] == Copied(
        source=folder / "skills/probe/reference.md"
    )


def test_overlay_output_matches_project_output_for_the_shared_destinations(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    (project / "loadout").mkdir(parents=True)
    (project / "loadout/config.toml").write_text(
        'harnesses = ["claude", "codex", "droid", "opencode", "pi"]\n', encoding="utf-8"
    )
    folder = make_overlay(tmp_path / "ov", "\n[claude]\n[codex]\n[droid]\n[opencode]\n[pi]\n")
    for name in ("permissions.toml", "mcp.toml"):
        shutil.copy(FIXTURES / "project" / name, project / "loadout" / name)
        shutil.copy(FIXTURES / "project" / name, folder / name)
    shutil.copytree(FIXTURES / "project/skills", project / "loadout/skills")
    shutil.copytree(FIXTURES / "project/skills", folder / "skills")

    expected = render_project(project)
    produced = render(folder, project)
    shared = [
        path
        for path in produced
        if str(path.relative_to(project)).startswith(
            (".pi/", ".factory/mcp.json", ".agents/", ".factory/skills", ".claude/skills")
        )
    ]
    assert {path.relative_to(project) for path in shared} >= {
        Path(".pi/mcp.json"),
        Path(".factory/mcp.json"),
        Path(".pi/extensions/pi-permission-system/config.json"),
        Path(".agents/skills/probe/SKILL.md"),
    }
    assert {str(path.relative_to(project).parts[0:2]) for path in shared} >= {
        str((".factory", "skills")),
        str((".claude", "skills")),
    }
    for path in shared:
        assert path in expected, path
        assert content(produced[path]) == content(expected[path]), path


def last_match(maps: list[dict[str, str]], command: str) -> str | None:
    # OpenCode concatenates permission rules from every config file and the last match wins (P9)
    decision = None
    for rules in maps:
        for pattern, verdict in rules.items():
            if fnmatch.fnmatchcase(command, pattern):
                decision = verdict
    return decision


CODEX_SERVERS = parse_servers(FIXTURES / "overlay/mcp.toml")


def test_codex_config_holds_the_joined_fragments_and_the_overlays_servers() -> None:
    text, notes = render_codex_overlay_config(
        "One.\n\nTwo.", Rules(mcp_allow=("docs/search",)), CODEX_SERVERS
    )
    parsed = tomllib.loads(text)
    assert parsed["developer_instructions"] == "One.\n\nTwo."
    assert list(parsed["mcp_servers"]) == ["docs", "local-tool"]
    assert parsed["mcp_servers"]["docs"]["tools"]["search"]["approval_mode"] == "approve"
    assert notes == ()


def test_approval_rules_for_a_server_the_overlay_does_not_define_get_a_note_and_no_table() -> None:
    text, notes = render_codex_overlay_config(
        None, Rules(mcp_allow=("elsewhere/search",)), CODEX_SERVERS
    )
    assert sorted(tomllib.loads(text)["mcp_servers"]) == ["docs", "local-tool"]
    assert notes == (
        (
            "codex: approval rules for MCP server elsewhere are not written, because the overlay "
            "does not define it and a project table would merge into the user's server"
        ),
    )


def test_codex_rules_file_starts_with_the_header_and_parses(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[codex]\n",
        {"permissions.toml": '[shell]\nallow = ["ls"]\ndeny = ["rm -rf"]\n'},
    )
    target = tmp_path / "t"
    document = str(render(folder, target)[target / ".codex/rules/app.rules"])
    assert document.startswith(CODEX_OVERLAY_HEADER + "\n")
    assert [rule.decision for rule in codex_rules(document, "app.rules")] == ["allow", "forbidden"]


def test_codex_writes_no_rules_file_without_a_shell_rule(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[codex]\ninstructions = ['one']\n",
        {"instructions/one.md": "x\n", "permissions.toml": '[mcp]\nallow = ["docs/search"]\n'},
    )
    target = tmp_path / "t"
    assert relative(render(folder, target), target) == {".codex/config.toml"}


def test_overlay_opencode_writes_the_v1_permission_key() -> None:
    owned = opencode_owned(Rules(allow=("ls",), mcp_ask=("docs/search",)), {}, None)
    assert owned.keys == (
        (
            ("permission",),
            {"bash": {"ls": "allow", "ls *": "allow"}, "docs_search": "ask"},
        ),
    )
    assert "*" not in owned.keys[0][1]["bash"]


def test_a_user_level_opencode_allow_survives_the_overlays_permissions() -> None:
    user = {"*": "allow", "touch markerU": "deny"}
    overlay = render_opencode_overlay_permission(Rules(deny=("rm -rf",), ask=("git push",)))["bash"]
    assert overlay == {"rm -rf": "deny", "rm -rf *": "deny", "git push": "ask", "git push *": "ask"}
    maps = [user, overlay]
    assert last_match(maps, "ls -la") == "allow"
    assert last_match(maps, "touch markerU") == "deny"
    assert last_match(maps, "rm -rf build") == "deny"
    assert last_match(maps, "git push origin") == "ask"


def test_an_overlay_allow_overrides_a_conflicting_user_level_deny() -> None:
    user = {"ls -la": "deny", "touch markerU": "deny"}
    overlay = render_opencode_overlay_permission(Rules(allow=("ls",)))["bash"]
    assert overlay == {"ls": "allow", "ls *": "allow"}
    maps = [user, overlay]
    assert last_match(maps, "ls -la") == "allow"
    assert last_match(maps, "touch markerU") == "deny"


def test_a_stated_shell_default_becomes_the_overlays_catch_all() -> None:
    bash = render_opencode_overlay_permission(Rules(allow=("ls",), default="deny"))["bash"]
    assert list(bash) == ["*", "ls", "ls *"]
    assert bash["*"] == "deny"


def test_opencode_instructions_name_a_file_in_the_overlay_folder(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[opencode]\ninstructions = ['one']\n",
        {"instructions/one.md": "First.\n"},
    )
    target = tmp_path / "t"
    rendered = render_overlay(load_overlay_manifest(folder), "app", target, NO_FACTS)
    path = folder / ".loadout/opencode-instructions.md"
    assert rendered.shared == {path: f"{HEADER}\n\nFirst.\n"}
    assert rendered.files[target / "opencode.json"] == OwnedJson(
        keys=((("instructions",), [str(path)]),), rules=()
    )


def test_the_overlay_notes_reach_the_render(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[codex]\n",
        {
            "permissions.toml": '[mcp]\nallow = ["elsewhere/search"]\n',
            "mcp.toml": '[svc]\ntransport = "http"\nurl = "https://svc/mcp"\n',
        },
    )
    rendered = render_overlay(load_overlay_manifest(folder), "app", tmp_path / "t", NO_FACTS)
    assert len(rendered.notes) == 1
    assert "MCP server elsewhere" in rendered.notes[0]


def test_a_foreign_server_note_is_kept_when_no_codex_config_renders(tmp_path: Path) -> None:
    folder = make_overlay(
        tmp_path / "ov",
        "\n[codex]\n",
        {"permissions.toml": '[mcp]\nallow = ["elsewhere/search"]\n'},
    )
    target = tmp_path / "t"
    rendered = render_overlay(load_overlay_manifest(folder), "app", target, NO_FACTS)
    assert len(rendered.notes) == 1
    assert "MCP server elsewhere" in rendered.notes[0]
    assert target / ".codex/config.toml" not in rendered.files
