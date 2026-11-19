from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loadout.emit import RenderContext, render_global, render_project, render_project_detailed
from loadout.errors import LoadoutError
from loadout.manifest import load_manifest, load_profile
from loadout.output import Copied, Merged, Output
from loadout.permissions.rules import Rules
from loadout.personal import clash_refusal, personal_tier
from loadout.project import load_project_config
from loadout.server_policy import ServerPolicy, aliased_rules, aliased_servers
from loadout.servers import Server
from loadout.skill_policy import SkillPolicy
from loadout.skills import discover_skills
from loadout.stealth_manifest import load_stealth_manifest
from loadout.stealth_registry import RegisteredStealth
from loadout.stealth_render import StealthOutput, TargetFacts, render_stealth
from registration import register
from test_personal_tier import _project, _register, _stealth
from test_stealth_kits import kit_source

NO_FACTS = TargetFacts(agents_md=False, claude_md_on_path=False)
FIGMA = '[figma]\ntransport = "http"\nurl = "https://figma.example/mcp"\n'
REVIEW = "---\nname: review\ndescription: Reviews.\n---\nReview body.\n"
NATIVE = "---\nname: native\ndescription: As written.\n---\r\nNative body.\n"


def _write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8", newline="")


def _text(output: Output | StealthOutput) -> str:
    if isinstance(output, Copied):
        return output.source.read_text(encoding="utf-8")
    assert isinstance(output, str)
    return output


def _skills(outputs: dict[Path, Any], base: Path) -> dict[str, Any]:
    return {
        str(path.relative_to(base)): output
        for path, output in outputs.items()
        if path.is_relative_to(base) and path.name == "SKILL.md"
    }


SKILL_ALIASES = (
    '[skills.review]\nname = "review-mine"\n\n'
    '[skills.native]\nname = "native-mine"\nrender = false\n'
)
SKILL_FILES = {"skills/review/SKILL.md": REVIEW, "skills/native/SKILL.md": NATIVE}


def _assert_aliased_skills(found: dict[str, Any]) -> None:
    assert sorted(found) == ["native-mine/SKILL.md", "review-mine/SKILL.md"]
    review = _text(found["review-mine/SKILL.md"])
    assert review.startswith("---\nname: review-mine\ndescription: Reviews.\n---\n")
    assert "skills/review/" in review
    assert isinstance(found["native-mine/SKILL.md"], str)
    assert found["native-mine/SKILL.md"] == NATIVE.replace("name: native", "name: native-mine")


def test_a_global_skill_alias_renames_the_folder_and_the_frontmatter(
    tmp_path: Path, fake_home: Path
) -> None:
    _write(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\n\n[claude]\n\n' + SKILL_ALIASES,
            **SKILL_FILES,
        },
    )
    register(tmp_path)

    outputs = render_global(tmp_path)

    _assert_aliased_skills(_skills(outputs, fake_home / ".claude/skills"))


def test_a_project_skill_alias_renames_its_own_and_its_kits_skills(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude"]\nkits = ["kit"]\n\n' + SKILL_ALIASES,
            "loadout/skills/review/SKILL.md": REVIEW,
            "loadout/kits/kit/skills/native/SKILL.md": NATIVE,
        },
    )

    outputs = render_project(tmp_path)

    _assert_aliased_skills(_skills(outputs, tmp_path / ".claude/skills"))


def test_a_stealth_skill_alias_renames_its_own_skills(tmp_path: Path) -> None:
    folder = _stealth(tmp_path, "mine", tmp_path / "app", "[claude]\n\n" + SKILL_ALIASES)
    _write(folder, SKILL_FILES)

    files = render_stealth(load_stealth_manifest(folder), "mine", tmp_path / "app", NO_FACTS).files

    _assert_aliased_skills(_skills(files, tmp_path / "app/.claude/skills"))


def test_a_stealth_skill_alias_renames_a_linked_kits_skill(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit_source(fake_home, monkeypatch)
    folder = _stealth(tmp_path, "mine", tmp_path / "app", '[skills.review]\nname = "review-mine"\n')
    toml = folder / "loadout.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("]\n", ']\nkits = ["mobile-app"]\n', 1)
        + "\n[claude]\n",
        encoding="utf-8",
    )

    files = render_stealth(load_stealth_manifest(folder), "mine", tmp_path / "app", NO_FACTS).files

    found = _skills(files, tmp_path / "app/.claude/skills")
    assert sorted(found) == ["kit-only/SKILL.md", "review-mine/SKILL.md"]
    assert _text(found["review-mine/SKILL.md"]).startswith("---\nname: review-mine\n")


def test_a_skill_policy_still_applies_to_the_original_name(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude", "droid"]\n\n'
            '[skills.review]\nname = "review-mine"\nagents = ["droid"]\n',
            "loadout/skills/review/SKILL.md": REVIEW,
        },
    )

    outputs = render_project(tmp_path)

    assert list(_skills(outputs, tmp_path)) == [".factory/skills/review-mine/SKILL.md"]


def test_a_global_server_alias_renames_the_server(tmp_path: Path, fake_home: Path) -> None:
    _write(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\n\n[pi]\n\n'
            '[servers.figma]\nname = "figma-haufe"\n',
            "mcp.toml": FIGMA,
        },
    )
    register(tmp_path)

    outputs = render_global(tmp_path)

    pi = next(_text(o) for p, o in outputs.items() if p.name == "mcp.json")
    assert list(json.loads(pi)["mcpServers"]) == ["figma-haufe"]


def test_a_project_server_alias_renames_its_own_server(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["cursor"]\n\n[servers.figma]\nname = "figma-haufe"\n',
            "loadout/mcp.toml": FIGMA,
        },
    )

    outputs = render_project(tmp_path)

    servers = json.loads(_text(outputs[tmp_path / ".cursor/mcp.json"]))["mcpServers"]
    assert servers == {"figma-haufe": {"url": "https://figma.example/mcp"}}


def test_a_stealth_server_alias_renames_its_own_server(tmp_path: Path) -> None:
    folder = _stealth(
        tmp_path,
        "mine",
        tmp_path / "app",
        '[claude]\n\n[pi]\n\n[servers.figma]\nname = "figma-haufe"\n',
        {"mcp.toml": FIGMA},
    )

    render = render_stealth(load_stealth_manifest(folder), "mine", tmp_path / "app", NO_FACTS)

    pi = json.loads(_text(render.files[tmp_path / "app/.pi/mcp.json"]))
    assert list(pi["mcpServers"]) == ["figma-haufe"]
    assert list(render.claude_servers) == ["figma-haufe"]


def test_a_stealth_kits_server_and_its_rule_follow_the_alias(
    fake_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = kit_source(fake_home, monkeypatch)
    (source / "loadout/kits/permissions/mobile.toml").write_text(
        '[mcp]\nallow = ["device/*"]\n', encoding="utf-8"
    )
    folder = _stealth(
        tmp_path,
        "mine",
        tmp_path / "app",
        '[pi]\n\n[servers.device]\nname = "device-mine"\n',
        {"permissions.toml": '[mcp]\nask = ["device-mine/reset"]\n'},
    )
    toml = folder / "loadout.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("]\n", ']\nkits = ["mobile-app"]\n', 1),
        encoding="utf-8",
    )

    stealth = load_stealth_manifest(folder)

    assert list(stealth.servers) == ["device-mine", "docs"]
    assert stealth.rules.mcp_allow == ("device-mine/*",)
    assert stealth.rules.mcp_ask == ("device-mine/reset",)


Q8_FILES = {
    "loadout/config.toml": 'harnesses = ["claude", "pi"]\nkits = ["design"]\n\n'
    '[servers.figma]\nname = "figma-haufe"\n',
    "loadout/kits/design/mcp.toml": FIGMA,
    "loadout/kits/design/permissions.toml": '[mcp]\nallow = ["figma/*"]\n',
    "loadout/permissions.toml": '[mcp]\nask = ["figma/get_code"]\n',
}


def test_a_kits_rule_follows_its_renamed_server_and_the_projects_own_rule_is_kept(
    tmp_path: Path,
) -> None:
    _write(tmp_path, Q8_FILES)

    outputs = render_project(tmp_path)

    claude = json.loads(_text(outputs[tmp_path / ".claude/settings.json"]))["permissions"]
    assert claude["allow"] == ["mcp__figma-haufe__*"]
    assert claude["ask"] == ["mcp__figma__get_code"]
    assert list(json.loads(_text(outputs[tmp_path / ".mcp.json"]))["mcpServers"]) == ["figma-haufe"]
    assert list(json.loads(_text(outputs[tmp_path / ".pi/mcp.json"]))["mcpServers"]) == [
        "figma-haufe"
    ]
    pi = json.loads(_text(outputs[tmp_path / ".pi/extensions/pi-permission-system/config.json"]))
    assert pi["permission"] == {
        "bash": {},
        "mcp__figma_haufe__*": "allow",
        "mcp__figma__get_code": "ask",
    }


def test_without_the_alias_the_kits_rule_keeps_the_original_server_name(tmp_path: Path) -> None:
    files = dict(Q8_FILES)
    files["loadout/config.toml"] = 'harnesses = ["claude"]\nkits = ["design"]\n'
    _write(tmp_path, files)

    outputs = render_project(tmp_path)

    claude = json.loads(_text(outputs[tmp_path / ".claude/settings.json"]))["permissions"]
    assert claude["allow"] == ["mcp__figma__*"]
    assert list(json.loads(_text(outputs[tmp_path / ".mcp.json"]))["mcpServers"]) == ["figma"]


def test_a_global_sources_rule_follows_a_server_it_defines_but_the_own_folders_does_not(
    tmp_path: Path, fake_home: Path
) -> None:
    _write(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "vendor"\npath = "vendor"\n\n'
            '[[source]]\nname = "own"\npath = "."\n\n[claude]\n\n'
            '[servers.figma]\nname = "figma-haufe"\n',
            "vendor/mcp.toml": FIGMA,
            "vendor/permissions.toml": '[mcp]\nallow = ["figma/*"]\n',
            "mcp.toml": FIGMA,
            "permissions.toml": '[mcp]\nask = ["figma/get_code"]\n',
        },
    )
    register(tmp_path)

    outputs = render_global(tmp_path)

    settings = json.loads(_text(outputs[fake_home / ".claude/settings.json"]))["permissions"]
    assert settings["allow"] == ["mcp__figma-haufe__*"]
    assert settings["ask"] == ["mcp__figma__get_code"]
    claude_json = outputs[fake_home / ".claude.json"]
    assert isinstance(claude_json, Merged)
    assert list(json.loads(claude_json.document)["mcpServers"]) == ["figma-haufe"]


def test_a_personal_server_alias_avoids_a_clash_with_the_teams_server(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": FIGMA})
    folder = _stealth(
        fake_home,
        "mine",
        root,
        '[pi]\n\n[servers.figma]\nname = "figma-mine"\n',
        {"mcp.toml": FIGMA},
    )
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, RenderContext(personal=personal_tier(root)))

    servers = json.loads(_text(render.outputs[root / ".pi/mcp.json"]))["mcpServers"]
    assert list(servers) == ["figma", "figma-mine"]
    assert render.report.refusals == ()


def test_without_the_alias_the_personal_server_clashes(fake_home: Path) -> None:
    root = _project(fake_home / "work/app", '"pi"', {"mcp.toml": FIGMA})
    folder = _stealth(fake_home, "mine", root, "[pi]\n", {"mcp.toml": FIGMA})
    _register(RegisteredStealth("mine", folder))

    render = render_project_detailed(root, RenderContext(personal=personal_tier(root)))

    assert render.report.refusals == (
        clash_refusal("mine", "server", "figma", folder / "loadout.toml"),
    )


def test_a_skill_alias_parses_in_a_claude_only_project(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('harnesses = ["claude"]\n\n[skills.x]\nname = "y"\n', encoding="utf-8")

    assert load_project_config(path).skill_policies == (SkillPolicy("x", None, True, "y"),)


def test_skills_flag_whether_they_come_from_a_local_folder(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"plain/SKILL.md": REVIEW, "mine.local/SKILL.md": REVIEW, "both/SKILL.md": REVIEW},
    )

    found = {skill.name: skill.local for skill in discover_skills(tmp_path)}

    assert found == {"both": False, "mine": True, "plain": False}


def test_an_empty_agents_list_in_a_project_config_renders_the_skill_nowhere(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude"]\n\n[skills.review]\nagents = []\n',
            "loadout/skills/review/SKILL.md": REVIEW,
            "loadout/skills/native/SKILL.md": NATIVE,
        },
    )

    outputs = render_project(tmp_path)

    assert sorted(_skills(outputs, tmp_path / ".claude/skills")) == ["native/SKILL.md"]


def test_a_server_alias_parses_in_the_project_and_the_global_manifest(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'harnesses = ["claude"]\n\n[servers.figma]\nname = "figma-haufe"\n', encoding="utf-8"
    )
    manifest = tmp_path / "loadout.toml"
    manifest.write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\n\n[servers.figma]\nname = "figma-haufe"\n',
        encoding="utf-8",
    )

    assert load_project_config(config).server_policies == (ServerPolicy("figma", "figma-haufe"),)
    assert load_manifest(manifest).server_policies == (ServerPolicy("figma", "figma-haufe"),)


REFUSALS = [
    pytest.param(
        '[skills.x]\nname = "a/b"\n',
        r"skills\.x\.name 'a/b' is not a usable name",
        id="invalid-name",
    ),
    pytest.param(
        '[servers.figma]\nname = "figma"\n',
        r"servers\.figma\.name repeats its own name; remove it",
        id="alias-equals-original",
    ),
    pytest.param(
        '[skills.a]\nname = "c"\n\n[skills.b]\nname = "c"\n',
        r"skills\.a\.name and skills\.b\.name both rename onto 'c'; pick a different name",
        id="two-aliases-onto-one-name",
    ),
    pytest.param(
        '[servers.a]\nname = "b"\n\n[servers.b]\nname = "c"\n',
        r"servers\.a\.name renames onto 'b', which \[servers\.b\] already names",
        id="alias-onto-another-entry",
    ),
    pytest.param(
        '[servers.figma]\nurl = "https://x"\n',
        r"servers\.figma: unrecognised key\(s\) url; 'name' is the only key it accepts",
        id="unknown-server-key",
    ),
    pytest.param(
        '[skills.x]\nalias = "y"\n',
        r"skills\.x: unrecognised key\(s\) alias",
        id="unknown-skill-key",
    ),
    pytest.param(
        "[servers.figma]\n",
        r"servers\.figma is empty; set 'name', or remove the table",
        id="empty-server-table",
    ),
    pytest.param(
        "[skills.x]\n",
        r"skills\.x is empty; set 'agents', 'render' or 'name', or remove the table",
        id="empty-skill-table",
    ),
]


@pytest.mark.parametrize(("body", "message"), REFUSALS)
def test_a_bad_alias_in_the_project_config_is_refused_naming_the_file(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('harnesses = ["claude"]\n\n' + body, encoding="utf-8")

    with pytest.raises(LoadoutError, match=f"^{path}: {message}"):
        load_project_config(path)


@pytest.mark.parametrize(("body", "message"), REFUSALS)
def test_a_bad_alias_in_the_global_manifest_is_refused_naming_the_file(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "loadout.toml"
    path.write_text('[[source]]\nname = "s"\npath = "."\n\n[claude]\n\n' + body, encoding="utf-8")

    with pytest.raises(LoadoutError, match=f"^{path}: {message}"):
        load_manifest(path)


@pytest.mark.parametrize(("body", "message"), REFUSALS)
def test_a_bad_alias_in_a_stealth_manifest_is_refused_naming_the_file(
    tmp_path: Path, body: str, message: str
) -> None:
    folder = _stealth(tmp_path, "mine", tmp_path / "app", "[claude]\n\n" + body)

    with pytest.raises(LoadoutError, match=f"^{folder / 'loadout.toml'}: {message}"):
        load_stealth_manifest(folder)


def test_a_skill_alias_onto_another_skills_name_is_refused_naming_the_file(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude"]\n\n[skills.review]\nname = "other"\n',
            "loadout/skills/review/SKILL.md": REVIEW,
            "loadout/skills/other/SKILL.md": "---\nname: other\n---\nOther.\n",
        },
    )

    with pytest.raises(
        LoadoutError,
        match=f"^{tmp_path / 'loadout/config.toml'}: skills\\.review\\.name renames onto "
        "'other', the name of another skill",
    ):
        render_project(tmp_path)


def test_a_server_alias_onto_another_servers_name_is_refused_naming_the_file(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["cursor"]\n\n[servers.figma]\nname = "other"\n',
            "loadout/mcp.toml": FIGMA + '\n[other]\ntransport = "http"\nurl = "https://o"\n',
        },
    )

    with pytest.raises(
        LoadoutError,
        match=f"^{tmp_path / 'loadout/config.toml'}: servers\\.figma\\.name renames onto "
        "'other', the name of another server",
    ):
        render_project(tmp_path)


def test_aliased_servers_keeps_the_position_of_a_renamed_server() -> None:
    servers = {
        name: Server(name=name, transport="http", url=f"https://{name}")
        for name in ("a", "figma", "z")
    }

    renamed = aliased_servers(servers, (ServerPolicy("figma", "figma-haufe"),))

    assert list(renamed) == ["a", "figma-haufe", "z"]
    assert renamed["figma-haufe"].name == "figma-haufe"
    assert renamed["figma-haufe"].url == "https://figma"


def test_aliased_rules_rewrites_only_the_renamed_servers_entries() -> None:
    rules = Rules(
        allow=("figma/x",),
        mcp_allow=("figma/*", "other/*"),
        mcp_deny=("figma/delete",),
        mcp_ask=("figmatic/read",),
    )

    renamed = aliased_rules(rules, {"figma": "figma-haufe"})

    assert renamed == Rules(
        allow=("figma/x",),
        mcp_allow=("figma-haufe/*", "other/*"),
        mcp_deny=("figma-haufe/delete",),
        mcp_ask=("figmatic/read",),
    )


def test_an_alias_refusal_under_extends_names_every_file_in_the_chain(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\n\n[claude]\n\n'
            '[servers.figma]\nname = "figma"\n',
            "work.toml": 'extends = "default"\n',
        },
    )

    with pytest.raises(LoadoutError) as raised:
        load_profile(tmp_path, "work")

    message = str(raised.value)
    assert (tmp_path / "work.toml").name in message
    assert str(tmp_path.resolve() / "loadout.toml") in message
    assert "servers.figma.name repeats its own name" in message
