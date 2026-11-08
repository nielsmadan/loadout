from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.output import Copied
from loadout.stealth_check import _renderer_notes
from loadout.stealth_documents import OwnedJson
from loadout.stealth_manifest import load_stealth_manifest
from loadout.stealth_render import TargetFacts, render_stealth
from loadout.stealth_sync import _collisions

NO_FACTS = TargetFacts(agents_md=False, claude_md_on_path=False)


def kit_source(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A machine config whose global source offers the catalog kit `mobile-app`."""
    source = home / "ac"
    kits = source / "loadout" / "kits"
    for folder in (
        "instructions",
        "permissions",
        "mcp",
        "skills/review/references",
        "skills/kit-only",
    ):
        (kits / folder).mkdir(parents=True, exist_ok=True)
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
        encoding="utf-8",
    )
    (kits / "mobile-app.toml").write_text(
        'instructions = ["mobile"]\npermissions = ["mobile"]\nmcp = ["device"]\n'
        'skills = ["review", "kit-only"]\n',
        encoding="utf-8",
    )
    (kits / "instructions" / "mobile.md").write_text("Kit mobile rules.\n", encoding="utf-8")
    (kits / "permissions" / "mobile.toml").write_text(
        '[shell]\nallow = ["agent-device", "agent-device-info"]\n', encoding="utf-8"
    )
    (kits / "mcp" / "device.toml").write_text(
        '[device]\ntransport = "http"\nurl = "https://kit.example/mcp"\n\n'
        '[docs]\ntransport = "http"\nurl = "https://kit-docs.example/mcp"\n',
        encoding="utf-8",
    )
    (kits / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: Kit review.\n---\nKit review body.\n", encoding="utf-8"
    )
    (kits / "skills" / "kit-only" / "SKILL.md").write_text(
        "---\nname: kit-only\ndescription: Only the kit has it.\n---\nKit-only body.\n",
        encoding="utf-8",
    )
    (kits / "skills" / "review" / "references" / "list.md").write_text(
        "kit list\n", encoding="utf-8"
    )
    xdg = home / ".config"
    (xdg / "loadout").mkdir(parents=True, exist_ok=True)
    (xdg / "loadout" / "config.toml").write_text(f'source = "{source}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return source


def render(folder: Path, target: Path) -> dict[str, object]:
    files = render_stealth(load_stealth_manifest(folder), "app", target, NO_FACTS).files
    return {str(path.relative_to(target)): output for path, output in files.items()}


def text(output: object) -> str:
    if isinstance(output, str):
        return output
    if isinstance(output, Copied):
        return output.source.read_text(encoding="utf-8")
    assert isinstance(output, OwnedJson)
    return json.dumps(output.keys) + json.dumps(output.rules)


@pytest.fixture
def kits(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return kit_source(fake_home, monkeypatch)


def make(tmp_path: Path, harness_tables: str, files: dict[str, str] | None = None) -> Path:
    folder = tmp_path / "stealth"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "loadout.toml").write_text(
        '[stealth]\ntargets = ["~/app*"]\nkits = ["mobile-app"]\n\n'
        '[[source]]\nname = "app"\npath = "."\n' + harness_tables,
        encoding="utf-8",
    )
    for name, content in (files or {}).items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(content, encoding="utf-8")
    return folder


def test_a_linked_kit_renders_every_slice_it_offers(kits: Path, tmp_path: Path) -> None:
    folder = make(tmp_path, "\n[claude]\n[codex]\n[droid]\n[opencode]\n[pi]\n")
    files = render(folder, tmp_path / "app")
    assert "Kit mobile rules." in text(files["CLAUDE.local.md"])
    assert "Kit mobile rules." in text(files[".factory/AGENTS.md"])
    assert "Kit mobile rules." in text(files[".pi/APPEND_SYSTEM.md"])
    assert "Kit mobile rules." in text(files[".codex/config.toml"])
    assert "https://kit.example/mcp" in text(files[".codex/config.toml"])
    assert "agent-device" in text(files[".claude/settings.local.json"])
    assert "agent-device" in text(files[".factory/settings.local.json"])
    assert "Kit review body." in text(files[".claude/skills/review/SKILL.md"])
    assert text(files[".claude/skills/review/references/list.md"]) == "kit list\n"
    assert "https://kit.example/mcp" in text(files[".factory/mcp.json"])
    assert "https://kit.example/mcp" in text(files[".pi/mcp.json"])
    assert "agent-device" in text(files["opencode.json"])
    for skills in (".claude/skills", ".agents/skills", ".factory/skills"):
        assert "Kit review body." in text(files[f"{skills}/review/SKILL.md"])
    rendered = render_stealth(load_stealth_manifest(folder), "app", tmp_path / "app", NO_FACTS)
    assert "Kit mobile rules." in rendered.shared[folder / ".loadout/opencode-instructions.md"]


def test_the_stealth_sources_own_fragments_come_before_the_kits(kits: Path, tmp_path: Path) -> None:
    folder = make(
        tmp_path,
        '\n[all]\ninstructions = ["mine"]\n\n[claude]\n',
        {"instructions/mine.md": "My own rules.\n"},
    )
    document = text(render(folder, tmp_path / "app")["CLAUDE.local.md"])
    assert document.index("My own rules.") < document.index("Kit mobile rules.")


def test_the_cursor_rule_carries_the_kits_parts_after_the_sources_own(
    kits: Path, tmp_path: Path
) -> None:
    folder = make(
        tmp_path,
        '\n[cursor]\ninstructions = ["mine"]\n',
        {"instructions/mine.md": "My own rules.\n"},
    )
    files = render(folder, tmp_path / "app")
    rule = text(files[".cursor/rules/app.mdc"])
    assert rule.startswith("---\nalwaysApply: true\n---\n\n")
    assert rule.index("My own rules.") < rule.index("Kit mobile rules.")
    assert "agent-device" in text(files[".cursor/cli.json"])
    assert "https://kit.example/mcp" in text(files[".cursor/mcp.json"])


def test_a_cursor_block_naming_no_instructions_still_gets_the_kits_rule(
    kits: Path, tmp_path: Path
) -> None:
    rule = text(render(make(tmp_path, "\n[cursor]\n"), tmp_path / "app")[".cursor/rules/app.mdc"])
    assert "Kit mobile rules." in rule


def test_the_stealth_sources_own_skill_replaces_the_kits(kits: Path, tmp_path: Path) -> None:
    folder = make(
        tmp_path,
        "\n[claude]\n",
        {"skills/review/SKILL.md": "---\nname: review\ndescription: Mine.\n---\nMy review.\n"},
    )
    files = render(folder, tmp_path / "app")
    assert "My review." in text(files[".claude/skills/review/SKILL.md"])
    assert "Kit-only body." in text(files[".claude/skills/kit-only/SKILL.md"])
    assert ".claude/skills/review/references/list.md" not in files


def test_the_stealth_sources_own_server_replaces_the_kits_and_keeps_its_others(
    kits: Path, tmp_path: Path
) -> None:
    folder = make(
        tmp_path,
        "\n[droid]\n",
        {"mcp.toml": '[device]\ntransport = "http"\nurl = "https://mine.example/mcp"\n'},
    )
    servers = json.loads(text(render(folder, tmp_path / "app")[".factory/mcp.json"]))
    assert servers["mcpServers"]["device"]["url"] == "https://mine.example/mcp"
    assert servers["mcpServers"]["docs"]["url"] == "https://kit-docs.example/mcp"


def test_a_deny_in_the_stealth_source_beats_the_kits_allow(kits: Path, tmp_path: Path) -> None:
    folder = make(
        tmp_path,
        "\n[claude]\n",
        {"permissions.toml": '[shell]\nallow = ["git status"]\ndeny = ["agent-device"]\n'},
    )
    settings = render(folder, tmp_path / "app")[".claude/settings.local.json"]
    assert isinstance(settings, OwnedJson)
    rules = {path[-1]: entries for path, entries in settings.rules}
    assert rules["allow"] == ("Bash(agent-device-info:*)", "Bash(git status:*)")
    assert rules["deny"] == ("Bash(agent-device:*)",)


@pytest.mark.parametrize(
    ("tables", "kept_out", "kept"),
    [
        (
            "\n[all]\ninstructions = false\n\n[claude]\n[droid]\n",
            ("CLAUDE.local.md", ".factory/AGENTS.md"),
            None,
        ),
        ("\n[codex]\ninstructions = false\n\n[pi]\n", (), ".pi/APPEND_SYSTEM.md"),
    ],
)
def test_instructions_false_in_all_or_in_codex_keeps_the_kits_parts_out(
    kits: Path, tmp_path: Path, tables: str, kept_out: tuple[str, ...], kept: str | None
) -> None:
    files = render(make(tmp_path, tables), tmp_path / "app")
    assert all(name not in files for name in kept_out)
    assert "Kit mobile rules." not in text(files.get(".codex/config.toml", ""))
    if kept is not None:
        assert "Kit mobile rules." in text(files[kept])


def test_skills_false_takes_no_kit_skill_and_keeps_the_kits_other_slices(
    kits: Path, tmp_path: Path
) -> None:
    files = render(make(tmp_path, "\n[claude]\nskills = false\n"), tmp_path / "app")
    assert sorted(files) == [".claude/settings.local.json", "CLAUDE.local.md"]


def test_instructions_false_keeps_the_kits_parts_out_of_that_harness(
    kits: Path, tmp_path: Path
) -> None:
    folder = make(tmp_path, "\n[claude]\ninstructions = false\n\n[droid]\n")
    files = render(folder, tmp_path / "app")
    assert "CLAUDE.local.md" not in files
    assert "Kit mobile rules." in text(files[".factory/AGENTS.md"])
    assert "agent-device" in text(files[".claude/settings.local.json"])


def test_a_harness_that_is_not_declared_gets_nothing_from_the_kit(
    kits: Path, tmp_path: Path
) -> None:
    folder = make(tmp_path, "\n[droid]\n")
    files = render(folder, tmp_path / "app")
    assert ".factory/AGENTS.md" in files
    assert not any(name.startswith(".claude") or name == "CLAUDE.local.md" for name in files)


def test_an_unknown_kit_is_refused_with_the_names_that_resolve(kits: Path, tmp_path: Path) -> None:
    folder = make(tmp_path, "\n[claude]\n")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("mobile-app", "tablet"), encoding="utf-8"
    )
    with pytest.raises(LoadoutError) as refused:
        _ = load_stealth_manifest(folder).kits
    assert "kit 'tablet' not found" in str(refused.value)
    assert "mobile-app" in str(refused.value)


@pytest.mark.parametrize(
    ("kits_line", "message"),
    [
        ('kits = "mobile-app"', "kits must be a list of kit names"),
        ('kits = ["mobile-app", "mobile-app"]', "lists mobile-app more than once"),
        ('kits = ["../up"]', "invalid kit name"),
    ],
)
def test_a_malformed_kits_list_is_refused(
    kits: Path, tmp_path: Path, kits_line: str, message: str
) -> None:
    folder = make(tmp_path, "\n[claude]\n")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('kits = ["mobile-app"]', kits_line),
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match=message):
        load_stealth_manifest(folder)


def test_a_top_level_kits_list_is_refused_with_where_it_belongs(kits: Path, tmp_path: Path) -> None:
    folder = tmp_path / "top"
    folder.mkdir()
    (folder / "loadout.toml").write_text(
        'kits = ["mobile-app"]\n\n[stealth]\ntargets = ["~/app*"]\n\n'
        '[[source]]\nname = "app"\npath = "."\n\n[claude]\n',
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match=r"kits belongs in the \[stealth\] table"):
        load_stealth_manifest(folder)


def test_a_vendored_hash_table_is_refused(kits: Path, tmp_path: Path) -> None:
    folder = make(tmp_path, '\n[kit.mobile-app]\nvendored = "sha256:00"\n\n[claude]\n')
    with pytest.raises(LoadoutError, match=r"\[kit\.\*\] is refused: a stealth source links"):
        load_stealth_manifest(folder)


@pytest.mark.parametrize("brought", ["hooks.json", "plugins.json"])
def test_a_kit_with_hooks_or_plugins_is_refused_naming_the_file(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, brought: str
) -> None:
    source = kit_source(fake_home, monkeypatch)
    hooked = source / "loadout" / "kits" / "hooked"
    hooked.mkdir()
    (hooked / "instructions.md").write_text("hooked\n", encoding="utf-8")
    (hooked / brought).write_text("{}\n", encoding="utf-8")
    folder = make(tmp_path, "\n[claude]\n")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("mobile-app", "hooked"), encoding="utf-8"
    )
    with pytest.raises(LoadoutError) as refused:
        _ = load_stealth_manifest(folder).kits
    assert "kit 'hooked' brings" in str(refused.value)
    assert str(hooked / brought) in str(refused.value)


def test_a_bundled_kit_links_without_a_machine_config(fake_home: Path, tmp_path: Path) -> None:
    folder = make(tmp_path, "\n[claude]\n")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("mobile-app", "frontend"), encoding="utf-8"
    )
    loaded = load_stealth_manifest(folder)
    assert loaded.kit_names == ("frontend",)
    assert "CLAUDE.local.md" in render(folder, tmp_path / "app")


def test_a_manifest_without_kits_loads_with_no_machine_config(
    fake_home: Path, tmp_path: Path
) -> None:
    folder = tmp_path / "plain"
    folder.mkdir()
    (folder / "loadout.toml").write_text(
        '[stealth]\ntargets = ["~/app*"]\n\n[[source]]\nname = "app"\npath = "."\n\n[claude]\n',
        encoding="utf-8",
    )
    loaded = load_stealth_manifest(folder)
    assert loaded.kit_names == ()
    assert loaded.harnesses == ("claude",)


def test_a_kit_server_named_like_a_user_level_one_refuses_the_stealth_source(
    kits: Path, fake_home: Path, tmp_path: Path
) -> None:
    codex = fake_home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('[mcp_servers.docs]\nurl = "https://other.example/mcp"\n', encoding="utf-8")
    manifest = load_stealth_manifest(make(tmp_path, "\n[codex]\n"))

    assert _collisions(manifest) == (
        (
            f"{codex} already defines the MCP server docs, which the stealth source also "
            "defines; rename one of them"
        ),
    )


def test_a_kit_rule_reaches_the_check_notes(kits: Path, tmp_path: Path) -> None:
    rules = kits / "loadout" / "kits" / "permissions" / "mobile.toml"
    rules.write_text('[shell]\nallow = ["git *"]\n', encoding="utf-8")
    manifest = load_stealth_manifest(make(tmp_path, "\n[droid]\n"))

    assert any("glob 'git *' is not rendered" in note for note in _renderer_notes(manifest))
