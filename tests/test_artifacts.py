from __future__ import annotations

import json
import stat
import tomllib
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from loadout.artifacts import (
    Artifact,
    ArtifactPart,
    artifact_destination,
    compose_document,
    load_artifacts,
    render_artifacts,
)
from loadout.emit import Copied, atomic_copy, render_all, render_global, render_project
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.manifest import load_manifest
from loadout.permissions.renderers import RENDERERS, JsonSpec, TextSpec
from loadout.permissions.rules import parse_rules
from loadout.project import ProjectConfig, load_project_config, project_outputs
from loadout.skill_installation import configured_skill_agents


def write(root: Path, name: str, content: str | bytes) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def project(root: Path, records: str, *, extra: str = "presets = false\n") -> Path:
    write(
        root,
        "loadout/config.toml",
        'harnesses = ["claude", "codex", "opencode", "pi"]\nartifacts = "artifacts.toml"\n' + extra,
    )
    return write(root, "loadout/artifacts.toml", records)


def document_record(parts: str, *, format_name: str = "json", extra: str = "") -> str:
    return (
        '[[artifact]]\nagents = ["claude"]\noutput = ".claude/settings.json"\n'
        f'format = "{format_name}"\n{extra}[artifact.parts]\n{parts}'
    )


def test_json_composition_preserves_literal_values_and_permission_order(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            'settings = {source = "settings/native.json"}\n'
            'permissions = {source = "permissions/native.json", keys = ["permission"]}\n',
            extra='order = ["permission", "missing", "model"]\n',
        ),
    )
    write(
        tmp_path,
        "loadout/settings/native.json",
        '{"model": null, "enabled": false, "names": [], "options": {}, "title": "µ"}',
    )
    write(
        tmp_path,
        "loadout/permissions/native.json",
        '{"permission": {"bash": {"*": "ask", "git *": "allow", "git reset *": "deny"}}}',
    )
    content = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(content, str)
    document = json.loads(content)
    assert list(document) == ["permission", "model", "enabled", "names", "options", "title"]
    assert document == {
        "permission": {"bash": {"*": "ask", "git *": "allow", "git reset *": "deny"}},
        "model": None,
        "enabled": False,
        "names": [],
        "options": {},
        "title": "µ",
    }
    assert list(document["permission"]["bash"]) == ["*", "git *", "git reset *"]


def test_toml_composition_keeps_nested_maps_arrays_and_top_level_order(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            'settings = {source = "settings.toml"}\nmcp = {source = "mcp.toml"}\n',
            format_name="toml",
            extra='order = ["mcp_servers", "enabled", "model", "tools"]\n',
        ),
    )
    write(tmp_path, "loadout/settings.toml", 'model = "x"\nenabled = false\ntools = []\n')
    write(
        tmp_path,
        "loadout/mcp.toml",
        '[mcp_servers.search]\ncommand = "uvx"\nargs = ["search", "--stdio"]\n'
        '[mcp_servers.search.env]\nMODE = "read"\n',
    )
    content = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(content, str)
    document = tomllib.loads(content)
    assert list(document) == ["mcp_servers", "enabled", "model", "tools"]
    assert document["mcp_servers"] == {
        "search": {"command": "uvx", "args": ["search", "--stdio"], "env": {"MODE": "read"}}
    }
    assert (document["enabled"], document["tools"]) == (False, [])


@pytest.mark.parametrize(
    "providers",
    [
        [{"name": "first"}],
        [{"name": "first"}, {"name": "second"}],
        [{"options": {"names": [{"name": "nested"}], "enabled": False}}],
        [[{"name": "nested"}], [], [{"options": {"values": [1, 2]}}]],
    ],
)
def test_toml_arrays_of_maps_preserve_key_order(providers: list[object]) -> None:
    artifact = Artifact(
        agents=("codex",),
        format="toml",
        parts=(ArtifactPart("settings", PurePosixPath("settings.toml")),),
        order=("providers", "enabled"),
    )
    expected = {"enabled": False, "providers": providers, "model": "chosen"}
    content = compose_document(artifact, (expected,))
    assert isinstance(content, str)
    document = tomllib.loads(content)
    assert list(document) == ["providers", "enabled", "model"]
    assert document == expected


@pytest.mark.parametrize("settings", ['{"permissions": null}', "{}"])
def test_explicit_owners_collide_even_when_one_is_empty(tmp_path: Path, settings: str) -> None:
    project(
        tmp_path,
        document_record(
            'settings = {source = "settings.json", keys = ["permissions"]}\n'
            'permissions = {source = "policy.json", keys = ["permissions"]}\n'
        ),
    )
    write(tmp_path, "loadout/settings.json", settings)
    write(tmp_path, "loadout/policy.json", "{}")
    with pytest.raises(LoadoutError, match=r"settings.json.*policy.json"):
        render_project(tmp_path)


def test_settings_cannot_take_a_dormant_category_key(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            'settings = {source = "settings.json"}\n'
            'hooks = {source = "hooks.json", keys = ["hooks"]}\n'
        ),
    )
    write(tmp_path, "loadout/settings.json", '{"model": "x", "hooks": {}}')
    write(tmp_path, "loadout/hooks.json", "{}")
    with pytest.raises(LoadoutError, match=r"settings.json.*hooks.json"):
        render_project(tmp_path)


def test_explicit_key_list_never_silently_discards_extra_fields(tmp_path: Path) -> None:
    project(tmp_path, document_record('settings = {source = "settings.json", keys = ["model"]}\n'))
    write(tmp_path, "loadout/settings.json", '{"model": "x", "enabled": false}')
    with pytest.raises(LoadoutError, match=r"settings.json.*enabled"):
        render_project(tmp_path)


def test_empty_documents_are_dormant_and_first_entries_activate_them(tmp_path: Path) -> None:
    index = project(
        tmp_path, document_record('hooks = {source = "hooks.json", keys = ["hooks"]}\n')
    )
    source = write(tmp_path, "loadout/hooks.json", "{}")
    assert render_project(tmp_path) == {}
    config = load_project_config(tmp_path / "loadout/config.toml")
    assert project_outputs(config) == (".claude/settings.json",)
    source.write_text('{"hooks": {}}')
    assert json.loads(str(render_project(tmp_path)[tmp_path / ".claude/settings.json"])) == {
        "hooks": {}
    }
    source.write_text("{}")
    index.write_text(
        document_record('hooks = {source = "hooks.json"}\n', extra="emit_empty = true\n")
    )
    assert render_project(tmp_path) == {tmp_path / ".claude/settings.json": "{}\n"}


@pytest.mark.parametrize("format_name, source", [("toml", ""), ("json", "{}")])
def test_empty_document_presence_is_explicit(tmp_path: Path, format_name: str, source: str) -> None:
    project(
        tmp_path,
        document_record(
            'settings = {source = "settings"}\n',
            format_name=format_name,
            extra="emit_empty = true\n",
        ),
    )
    write(tmp_path, "loadout/settings", source)
    assert set(render_project(tmp_path)) == {tmp_path / ".claude/settings.json"}


@pytest.mark.parametrize(
    "renderer",
    ["claude", "claude-project", "opencode", "pi", "pi-project", "claude-mcp-permissions"],
)
def test_permission_json_adapters_match_existing_renderers(tmp_path: Path, renderer: str) -> None:
    project(
        tmp_path,
        document_record(f'permissions = {{source = "policy.toml", renderer = "{renderer}"}}\n'),
    )
    path = write(
        tmp_path,
        "loadout/policy.toml",
        '[shell]\nallow = ["git status"]\ndeny = ["git reset"]\n[mcp]\nallow = ["search/read"]\n',
    )
    spec = RENDERERS[renderer]
    assert isinstance(spec, JsonSpec)
    content = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(content, str)
    expected = spec.fn(parse_rules(path), {})
    assert json.loads(content) == expected
    assert content == json.dumps(expected, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.parametrize("renderer", ["codex", "codex-project", "codex-mcp-permissions"])
def test_permission_text_adapters_match_existing_renderers(tmp_path: Path, renderer: str) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["codex"]\noutput = ".codex/rules/policy.rules"\n'
        f'format = "text"\ncategory = "permissions"\nsource = "policy.toml"\nrenderer = "{renderer}"\n',
    )
    path = write(
        tmp_path,
        "loadout/policy.toml",
        '[shell]\nallow = ["git status"]\n[mcp]\nallow = ["search/read"]\n',
    )
    spec = RENDERERS[renderer]
    assert isinstance(spec, TextSpec)
    assert render_project(tmp_path) == {
        tmp_path / ".codex/rules/policy.rules": spec.fn(parse_rules(path))
    }


def test_empty_renderer_reserves_its_keys_without_creating_an_output(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            'permissions = {source = "policy.toml", renderer = "claude"}\n'
            'settings = {source = "settings.json"}\n'
        ),
    )
    write(tmp_path, "loadout/policy.toml", "[shell]\nallow = []\n")
    settings = write(tmp_path, "loadout/settings.json", "{}")
    assert render_project(tmp_path) == {}
    settings.write_text('{"permissions": {}}')
    with pytest.raises(LoadoutError, match=r"policy.toml.*settings.json"):
        render_project(tmp_path)


@pytest.mark.parametrize(
    "format_name, renderer, extra",
    [
        ("json", "codex", ""),
        ("toml", "claude", ""),
        ("json", "claude-hooks", ""),
        ("json", "pi", 'settings = {source = "settings.json"}\n'),
    ],
)
def test_incompatible_or_whole_file_renderers_are_rejected(
    tmp_path: Path, format_name: str, renderer: str, extra: str
) -> None:
    index = project(
        tmp_path,
        document_record(
            f'permissions = {{source = "policy.toml", renderer = "{renderer}"}}\n' + extra,
            format_name=format_name,
        ),
    )
    with pytest.raises(LoadoutError, match="renderer"):
        load_artifacts(index)


def test_native_per_agent_instructions_and_skill_tree_copy_bytes_and_modes(tmp_path: Path) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["claude"]\noutput = "CLAUDE.md"\nformat = "copy"\n'
        'category = "instructions"\nsource = "instructions/claude.md"\n'
        '[[artifact]]\nagents = ["codex", "pi"]\noutput = "nested/AGENTS.override.md"\n'
        'format = "copy"\ncategory = "instructions"\nsource = "instructions/codex.md"\n'
        '[[artifact]]\nagents = ["pi"]\noutput = ".pi/skills/probe"\nformat = "tree"\n'
        'category = "skills"\nsource = "skills/pi/probe"\n',
    )
    write(tmp_path, "loadout/instructions/claude.md", b"Claude only\r\n")
    write(tmp_path, "loadout/instructions/codex.md", b"Codex and Pi\n")
    write(tmp_path, "loadout/skills/pi/probe/SKILL.md", b"---\nname: probe\n---\n")
    script = write(tmp_path, "loadout/skills/pi/probe/scripts/check", b"#!/bin/sh\necho hi\n")
    script.chmod(0o751)
    write(tmp_path, "loadout/skills/pi/probe/data/blob.bin", b"\x00\xff")
    write(tmp_path, "loadout/skills/pi/probe/.gitkeep", b"")
    outputs = render_project(tmp_path)
    assert set(outputs) == {
        tmp_path / "CLAUDE.md",
        tmp_path / "nested/AGENTS.override.md",
        tmp_path / ".pi/skills/probe/SKILL.md",
        tmp_path / ".pi/skills/probe/scripts/check",
        tmp_path / ".pi/skills/probe/data/blob.bin",
    }
    for destination, output in outputs.items():
        assert isinstance(output, Copied)
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_copy(destination, output.source)
        assert destination.read_bytes() == output.source.read_bytes()
        assert stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(
            output.source.stat().st_mode
        )
    config = load_project_config(tmp_path / "loadout/config.toml")
    assert config.artifacts is not None
    assert config.artifacts.agents("skills") == ("pi",)
    assert config.artifacts.agents("instructions") == ("claude", "codex", "pi")
    assert project_outputs(config, ["codex"]) == ("nested/AGENTS.override.md",)
    write(tmp_path, "loadout/skills/pi/probe/data/new", b"new")
    assert set(render_project(tmp_path)) == {*outputs, tmp_path / ".pi/skills/probe/data/new"}


@pytest.mark.parametrize("emit_empty, expected", [(False, False), (True, True)])
def test_empty_copy_presence_is_explicit(tmp_path: Path, emit_empty: bool, expected: bool) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["claude"]\noutput = "CLAUDE.md"\nformat = "copy"\ncategory = "instructions"\nsource = "instructions.md"\n'
        + f"emit_empty = {str(emit_empty).lower()}\n",
    )
    write(tmp_path, "loadout/instructions.md", b"")
    assert bool(render_project(tmp_path)) is expected


def test_missing_sources_require_explicit_optional_declarations(tmp_path: Path) -> None:
    records = document_record('settings = {source = "settings.json"}\n')
    index = project(tmp_path, records)
    with pytest.raises(LoadoutError, match=r"source not found.*settings.json"):
        render_project(tmp_path)
    index.write_text(document_record('settings = {source = "settings.json", optional = true}\n'))
    assert render_project(tmp_path) == {}


@pytest.mark.parametrize(
    "left, right", [("same", "same"), (".pi", ".pi/config.json"), (".pi/config.json", ".pi")]
)
def test_declared_routes_reject_duplicate_and_ancestor_collisions(
    tmp_path: Path, left: str, right: str
) -> None:
    records = "".join(
        f'[[artifact]]\nagents = ["pi"]\noutput = "{path}"\nformat = "tree"\ncategory = "skills"\nsource = "skills/{i}"\n'
        for i, path in enumerate((left, right))
    )
    index = project(tmp_path, records)
    with pytest.raises(LoadoutError, match="claimed by both"):
        render_artifacts(load_artifacts(index), project_root=tmp_path)


@pytest.mark.parametrize(
    "key, value",
    [
        ("source", "../outside"),
        ("source", "/outside"),
        ("output", "../outside"),
        ("output", "/outside"),
        ("source", "."),
        ("output", "."),
    ],
)
def test_paths_cannot_escape_their_roots(tmp_path: Path, key: str, value: str) -> None:
    fields = {"source": "instructions.md", "output": "CLAUDE.md", key: value}
    index = project(
        tmp_path,
        '[[artifact]]\nagents = ["claude"]\nformat = "copy"\ncategory = "instructions"\n'
        + "".join(f'{k} = "{v}"\n' for k, v in fields.items()),
    )
    with pytest.raises(LoadoutError, match="relative path"):
        load_artifacts(index)


def test_sources_and_outputs_cannot_overlap(tmp_path: Path) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["claude"]\nformat = "copy"\ncategory = "instructions"\nsource = "instructions.md"\noutput = "loadout/instructions.md"\n',
    )
    write(tmp_path, "loadout/instructions.md", "instructions")
    with pytest.raises(LoadoutError, match=r"source.*overlaps destination"):
        render_project(tmp_path)


@pytest.mark.parametrize(
    "destination", ["loadout/config.toml", "loadout/artifacts.toml", "loadout"]
)
def test_outputs_cannot_replace_their_configuration(tmp_path: Path, destination: str) -> None:
    project(
        tmp_path,
        f'[[artifact]]\nagents = ["claude"]\nformat = "tree"\ncategory = "skills"\nsource = "skills"\noutput = "{destination}"\n',
    )
    with pytest.raises(LoadoutError):
        render_project(tmp_path)


@pytest.mark.parametrize(
    "link_kind", ["source", "source-parent", "tree-child", "destination", "destination-parent"]
)
def test_symlinks_are_refused_before_native_deployment(tmp_path: Path, link_kind: str) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["pi"]\nformat = "tree"\ncategory = "skills"\nsource = "skills/probe"\noutput = ".pi/skills"\n',
    )
    real = tmp_path / "real"
    real.mkdir()
    write(real, "SKILL.md", "real content")
    source = tmp_path / "loadout/skills/probe"
    if link_kind == "source":
        source.parent.mkdir()
        source.symlink_to(real, target_is_directory=True)
    elif link_kind == "source-parent":
        (tmp_path / "loadout/skills").symlink_to(real, target_is_directory=True)
    else:
        write(source, "SKILL.md", "source content")
        if link_kind == "tree-child":
            (source / "linked").symlink_to(real, target_is_directory=True)
        elif link_kind == "destination":
            (tmp_path / ".pi").mkdir()
            (tmp_path / ".pi/skills").symlink_to(real, target_is_directory=True)
        else:
            (tmp_path / ".pi").symlink_to(real, target_is_directory=True)
    with pytest.raises(LoadoutError, match="symlink"):
        render_project(tmp_path)
    assert (real / "SKILL.md").read_text() == "real content"


def test_literal_json_rejects_duplicate_keys_and_non_json_values(tmp_path: Path) -> None:
    project(tmp_path, document_record('settings = {source = "settings.json"}\n'))
    for content in ('{"a": 1, "a": 2}', '{"a": NaN}', "[]"):
        write(tmp_path, "loadout/settings.json", content)
        with pytest.raises(LoadoutError, match=r"settings.json"):
            render_project(tmp_path)


def test_standalone_composition_is_pure() -> None:
    record = Artifact(
        agents=("claude",),
        format="json",
        output=PurePosixPath("settings.json"),
        parts=(ArtifactPart("settings", PurePosixPath("settings.json")),),
    )
    original = {"settings": {"one": [None, False, {}]}}
    assert compose_document(record, (original,)) == json.dumps(original, indent=2) + "\n"
    assert original == {"settings": {"one": [None, False, {}]}}


def test_rendering_never_uses_destination_content(tmp_path: Path) -> None:
    project(tmp_path, document_record('settings = {source = "settings.json"}\n'))
    write(tmp_path, "loadout/settings.json", '{"model": "chosen"}')
    expected = render_project(tmp_path)
    write(tmp_path, ".claude/settings.json", b"\xff\x00unparseable output")
    assert render_project(tmp_path) == expected


@pytest.mark.parametrize("declaration", ['instructions = ["intro"]', 'templates = ["web"]'])
def test_explicit_project_mode_refuses_ignored_legacy_inputs(
    tmp_path: Path, declaration: str
) -> None:
    project(tmp_path, "", extra="presets = false\n" + declaration + "\n")
    with pytest.raises(LoadoutError, match="legacy instructions or templates"):
        load_project_config(tmp_path / "loadout/config.toml")


def test_project_config_validates_direct_presets_and_agent_membership(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="presets must be a boolean"):
        ProjectConfig(harnesses=("claude",), presets="false")
    index = project(tmp_path, document_record('settings = {source = "settings.json"}\n'))
    artifacts = load_artifacts(index)
    with pytest.raises(LoadoutError, match=r"configured harnesses.*claude"):
        ProjectConfig(harnesses=("pi",), artifacts=artifacts)


def test_legacy_preset_and_artifact_cannot_claim_the_same_path(tmp_path: Path) -> None:
    project(tmp_path, document_record('settings = {source = "settings.json"}\n'), extra="")
    write(tmp_path, "loadout/permissions.toml", "")
    write(tmp_path, "loadout/settings.json", "{}")
    with pytest.raises(LoadoutError, match="claimed by both"):
        render_project(tmp_path)


def test_global_artifacts_follow_destination_templates_and_expose_skill_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "fake-pi"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(destination))
    write(source, "loadout.toml", 'artifacts = "artifacts.toml"\n')
    write(
        source,
        "artifacts.toml",
        '[[artifact]]\nagents = ["pi"]\nformat = "tree"\ncategory = "skills"\nsource = "skills"\ndestination = "${PI_CODING_AGENT_DIR}/skills"\n',
    )
    write(source, "skills/probe/SKILL.md", "native skill")
    manifest = load_manifest(source / "loadout.toml")
    assert manifest.sources == ()
    assert manifest.artifacts is not None
    assert artifact_destination(manifest.artifacts.records[0]) == destination / "skills"
    assert configured_skill_agents(source, "default") == ("pi",)
    outputs = render_global(source)
    assert outputs == {
        destination / "skills/probe/SKILL.md": Copied(source / "skills/probe/SKILL.md")
    }


def test_global_artifacts_collide_with_legacy_destinations(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "agent/AGENTS.md"
    write(
        source,
        "loadout.toml",
        'artifacts = "artifacts.toml"\n[[source]]\nname = "source"\npath = "."\n[instructions.pi]\norder = ["intro"]\ndestinations = ["'
        + str(destination)
        + '"]\n',
    )
    write(source, "instructions/intro.md", "legacy")
    write(
        source,
        "artifacts.toml",
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "instructions"\nsource = "native.md"\ndestination = "'
        + str(destination)
        + '"\n',
    )
    write(source, "native.md", "native")
    with pytest.raises(LoadoutError, match="claimed by both"):
        render_global(source)


def test_global_artifacts_cannot_overwrite_legacy_inputs(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write(
        source,
        "loadout.toml",
        'artifacts = "artifacts.toml"\n[[source]]\nname = "shared"\npath = "."\n'
        '[instructions.pi]\norder = ["intro"]\noutput = "generated.md"\n',
    )
    original = write(source, "instructions/intro.md", "legacy instructions")
    write(
        source,
        "artifacts.toml",
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "support"\n'
        'source = "replacement.md"\ndestination = "' + str(original) + '"\n',
    )
    write(source, "replacement.md", "replacement")
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_global(source)
    assert original.read_text() == "legacy instructions"


def test_project_artifacts_cannot_overwrite_legacy_permission_sources(tmp_path: Path) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "support"\n'
        'source = "replacement.toml"\noutput = "loadout/permissions.toml"\n',
        extra="",
    )
    original = write(tmp_path, "loadout/permissions.toml", '[shell]\nallow = ["pwd"]\n')
    write(tmp_path, "loadout/replacement.toml", '[shell]\nallow = ["ls"]\n')
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_project(tmp_path)
    assert original.read_text() == '[shell]\nallow = ["pwd"]\n'


@pytest.mark.parametrize(
    "destination", ["instructions/intro.md", "loadout.toml", "settings-base.json", "parent.toml"]
)
def test_project_artifacts_protect_global_source_dependencies(
    tmp_path: Path, destination: str
) -> None:
    write(
        tmp_path,
        "loadout.toml",
        '[[source]]\nname = "shared"\npath = "."\n'
        '[instructions.pi]\norder = ["intro"]\noutput = "generated.md"\n'
        '[permissions.claude]\nrender = "claude"\noutput = "generated.json"\n'
        'base = "settings-base.json"\n',
    )
    write(tmp_path, "parent.toml", 'extends = "default"\n')
    write(tmp_path, "work.toml", 'extends = "parent"\n')
    write(tmp_path, "instructions/intro.md", "legacy global instructions")
    write(tmp_path, "permissions.toml", '[shell]\nallow = ["pwd"]\n')
    write(tmp_path, "settings-base.json", '{"model": "legacy model"}')
    before = render_all(tmp_path, "work")
    assert "legacy global instructions" in str(before[tmp_path / "generated.md"])
    assert json.loads(str(before[tmp_path / "generated.json"]))["model"] == "legacy model"
    original = (tmp_path / destination).read_bytes()
    project(
        tmp_path,
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "support"\n'
        f'source = "replacement"\noutput = "{destination}"\n',
    )
    write(tmp_path, "loadout/replacement", "replacement")
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_all(tmp_path, "work")
    assert (tmp_path / destination).read_bytes() == original


@pytest.mark.parametrize("destination", ["loadout/config.toml", "loadout/instructions/intro.md"])
def test_global_artifacts_protect_project_source_dependencies(
    tmp_path: Path, destination: str
) -> None:
    write(
        tmp_path,
        "loadout/config.toml",
        'harnesses = ["pi"]\ninstructions = ["intro"]\n',
    )
    write(tmp_path, "loadout/permissions.toml", "")
    write(tmp_path, "loadout/instructions/intro.md", "legacy project instructions")
    assert "legacy project instructions" in str(render_all(tmp_path)[tmp_path / "AGENTS.md"])
    original = (tmp_path / destination).read_bytes()
    write(tmp_path, "loadout.toml", 'artifacts = "artifacts.toml"\n')
    write(
        tmp_path,
        "artifacts.toml",
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "support"\n'
        f'source = "replacement"\ndestination = "{tmp_path / destination}"\n',
    )
    write(tmp_path, "replacement", "replacement")
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_all(tmp_path)
    assert (tmp_path / destination).read_bytes() == original


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("dependency", ["instructions", "manifest", "machine-config"])
def test_artifacts_protect_declared_template_dependencies(
    tmp_path: Path, scope: str, dependency: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "machine"))
    library = tmp_path / "library"
    manifest = write(
        library,
        "loadout.toml",
        '[[source]]\nname = "shared"\npath = "."\n'
        '[instructions.pi]\norder = []\noutput = "generated.md"\n',
    )
    machine = write(tmp_path, "machine/loadout/config.toml", f'source = "{library}"\n')
    assert machine_config_path() == machine
    instructions = write(library, "templates/web/instructions.md", "declared template content")
    config = 'harnesses = ["pi"]\ntemplates = ["web"]\n'
    write(tmp_path, "loadout/config.toml", config)
    write(tmp_path, "loadout/permissions.toml", "")
    assert "declared template content" in str(render_all(tmp_path)[tmp_path / "AGENTS.md"])
    destination = {"instructions": instructions, "manifest": manifest, "machine-config": machine}[
        dependency
    ]
    original = destination.read_bytes()
    records = (
        '[[artifact]]\nagents = ["pi"]\nformat = "copy"\ncategory = "support"\n'
        'source = "replacement"\n'
    )
    if scope == "global":
        write(tmp_path, "loadout.toml", 'artifacts = "artifacts.toml"\n')
        write(tmp_path, "artifacts.toml", records + f'destination = "{destination}"\n')
        write(tmp_path, "replacement", "replacement")
    else:
        write(tmp_path, "loadout/config.toml", config + 'artifacts = "artifacts.toml"\n')
        write(
            tmp_path,
            "loadout/artifacts.toml",
            records + f'output = "{destination.relative_to(tmp_path)}"\n',
        )
        write(tmp_path, "loadout/replacement", "replacement")
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_all(tmp_path)
    assert destination.read_bytes() == original


def test_empty_global_and_project_routes_cannot_overlap(tmp_path: Path) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents = ["pi"]\nformat = "tree"\ncategory = "skills"\nsource = "skills"\noutput = ".pi/skills"\n',
    )
    (tmp_path / "loadout/skills").mkdir()
    write(tmp_path, "loadout.toml", 'artifacts = "global-artifacts.toml"\n')
    write(
        tmp_path,
        "global-artifacts.toml",
        '[[artifact]]\nagents = ["pi"]\nformat = "tree"\ncategory = "skills"\nsource = "global-skills"\ndestination = "'
        + str(tmp_path / ".pi")
        + '"\n',
    )
    (tmp_path / "global-skills").mkdir()
    with pytest.raises(LoadoutError, match="claimed by both"):
        render_all(tmp_path)


def test_artifact_reference_is_relative_to_owning_config_even_when_nested(tmp_path: Path) -> None:
    write(
        tmp_path,
        "loadout/config.toml",
        'harnesses = ["claude"]\npresets = false\nartifacts = "routes/native.toml"\n',
    )
    write(
        tmp_path,
        "loadout/routes/native.toml",
        document_record('settings = {source = "settings.json"}\n'),
    )
    write(tmp_path, "loadout/settings.json", '{"enabled": false}')
    assert render_project(tmp_path) == {
        tmp_path / ".claude/settings.json": '{\n  "enabled": false\n}\n'
    }


def test_artifact_configuration_can_be_reconstructed_under_a_fresh_root(tmp_path: Path) -> None:
    old = tmp_path / "old"
    fresh = tmp_path / "fresh"
    index = project(old, document_record('settings = {source = "settings.json"}\n'))
    write(old, "loadout/settings.json", '{"value": null}')
    artifacts = load_artifacts(index)
    write(fresh, "loadout/settings.json", '{"value": null}')
    rebuilt = replace(
        artifacts, source_root=fresh / "loadout", path=fresh / "loadout/artifacts.toml"
    )
    assert render_artifacts(rebuilt, project_root=fresh) == {
        fresh / ".claude/settings.json": '{\n  "value": null\n}\n'
    }
