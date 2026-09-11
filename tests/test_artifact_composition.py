import json
import stat
import tomllib
from pathlib import Path

import pytest

from loadout.artifacts import Merged, load_artifacts
from loadout.emit import check_all, render_global, render_project, write_all
from loadout.errors import LoadoutError
from loadout.extract import extract_codex
from test_artifacts import document_record, project, write
from test_deployment import cli

LAYERS = 'sources = [{source = "base.json"}, {source = "local.json", optional = true}]'


def layered_project(root: Path, *, extra: str = "", keys: str = "") -> Path:
    return project(
        root, document_record(f'settings = {{{LAYERS}, merge = "deep"{keys}}}\n', extra=extra)
    )


@pytest.mark.parametrize("format_name", ["json", "toml"])
def test_document_layers_preserve_order_and_use_deep_merge(
    tmp_path: Path, format_name: str
) -> None:
    parts = (
        f'settings = {{sources = [{{source = "base.{format_name}"}}, '
        f'{{source = "overlay.{format_name}"}}], merge = "deep"}}\n'
    )
    project(tmp_path, document_record(parts, format_name=format_name))
    if format_name == "json":
        base = '{"model":"base","nested":{"first":1,"second":2},"array":[1],"empty":[9]}'
        overlay = '{"model":"own","nested":{"second":3,"third":4},"array":[2,2],"empty":[]}'
    else:
        base = 'model="base"\narray=[1]\nempty=[9]\n[nested]\nfirst=1\nsecond=2\n'
        overlay = 'model="own"\narray=[2,2]\nempty=[]\n[nested]\nsecond=3\nthird=4\n'
    write(tmp_path, f"loadout/base.{format_name}", base)
    write(tmp_path, f"loadout/overlay.{format_name}", overlay)
    rendered = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(rendered, str)
    result = json.loads(rendered) if format_name == "json" else tomllib.loads(rendered)
    assert result == {
        "model": "own",
        "nested": {"first": 1, "second": 3, "third": 4},
        "array": [1, 2, 2],
        "empty": [9],
    }
    assert list(result) == list(json.loads(base) if format_name == "json" else tomllib.loads(base))
    assert list(result["nested"]) == ["first", "second", "third"]


def test_deleted_layer_keys_retain_partial_ownership(tmp_path: Path) -> None:
    layered_project(tmp_path, extra="partial = true\n")
    write(tmp_path, "loadout/base.json", '{"model":"base","retained":1}')
    write(tmp_path, "loadout/local.json", '{"model":null}')
    rendered = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(rendered, Merged)
    assert rendered.owned == frozenset({"model", "retained"})
    assert json.loads(rendered.document) == {"retained": 1}


def test_deleted_layer_key_cannot_hide_another_parts_ownership(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            f'settings = {{{LAYERS}, merge = "deep"}}\n'
            'hooks = {source = "hooks.json", keys = ["hooks"]}\n'
        ),
    )
    write(tmp_path, "loadout/base.json", '{"hooks":{"event":[]}}')
    write(tmp_path, "loadout/local.json", '{"hooks":null}')
    write(tmp_path, "loadout/hooks.json", "{}")
    with pytest.raises(LoadoutError, match="claimed by both") as caught:
        render_project(tmp_path)
    assert all(name in str(caught.value) for name in ("base.json", "local.json", "hooks.json"))


def test_every_layer_is_checked_against_explicit_keys(tmp_path: Path) -> None:
    layered_project(tmp_path, keys=', keys = ["model"]')
    write(tmp_path, "loadout/base.json", '{"hooks":[],"model":"base"}')
    write(tmp_path, "loadout/local.json", '{"hooks":null}')
    with pytest.raises(LoadoutError, match=r"base.json.*outside its ownership: hooks"):
        render_project(tmp_path)


@pytest.mark.parametrize("scope", ["project", "global"])
def test_optional_overlay_lifecycle_preserves_foreign_fields(
    tmp_path: Path, fake_home: Path, scope: str
) -> None:
    if scope == "project":
        layered_project(tmp_path, extra="partial = true\n")
        source_root = tmp_path / "loadout"
        output = tmp_path / ".claude/settings.json"
        index = source_root / "artifacts.toml"
    else:
        write(tmp_path, "loadout.toml", 'artifacts = "artifacts.toml"\n')
        index = write(
            tmp_path,
            "artifacts.toml",
            '[[artifact]]\nagents=["pi"]\ndestination="~/runtime.json"\n'
            'format="json"\npartial=true\n[artifact.parts]\n'
            f'settings = {{{LAYERS}, merge="deep"}}\n',
        )
        source_root, output = tmp_path, fake_home / "runtime.json"
    base = write(source_root, "base.json", '{"model":"base","budget":10}')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('{"runtime":42}')
    output.chmod(0o640)
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"runtime": 42, "model": "base", "budget": 10}
    local = write(source_root, "local.json", '{"model":"personal","budget":null}')
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"runtime": 42, "model": "personal"}
    assert cli(tmp_path, "check") == 0
    local.unlink()
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"runtime": 42, "model": "base", "budget": 10}
    write(source_root, "local.json", '{"budget":null,"model":null}')
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"runtime": 42}
    assert cli(tmp_path, "check") == 0
    base.write_text('{"model":"base"}')
    index.write_text("")
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"runtime": 42}
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert check_all(tmp_path) == []


def test_layered_output_drift_is_refused_and_authored_order_is_applied(tmp_path: Path) -> None:
    index = layered_project(tmp_path)
    write(tmp_path, "loadout/base.json", '{"model":"base","budget":10}')
    write_all(tmp_path)
    output = tmp_path / ".claude/settings.json"
    output.write_text('{"model":"manual","budget":10}')
    write(tmp_path, "loadout/local.json", '{"budget":20}')
    assert cli(tmp_path, "sync") == 1
    assert json.loads(output.read_text()) == {"model": "manual", "budget": 10}
    output.write_text('{\n  "model": "base",\n  "budget": 10\n}\n')
    index.write_text(
        index.read_text().replace('format = "json"', 'format = "json"\norder=["budget","model"]')
    )
    assert cli(tmp_path, "sync") == 0
    assert output.read_text() == '{\n  "budget": 20,\n  "model": "base"\n}\n'


@pytest.mark.parametrize("present", [False, True])
def test_every_input_path_is_protected_even_when_optional(
    tmp_path: Path, present: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    write(tmp_path, "loadout.toml", 'artifacts="artifacts.toml"\n')
    index = write(
        tmp_path,
        "artifacts.toml",
        f'[[artifact]]\nagents=["pi"]\ndestination="{tmp_path}/local.json"\n'
        'format="json"\n[artifact.parts]\n'
        f'settings = {{{LAYERS}, merge="deep"}}\n',
    )
    write(tmp_path, "base.json", '{"model":"base"}')
    optional = tmp_path / "local.json"
    if present:
        optional.write_text('{"model":"personal"}')
    loaded = load_artifacts(index, scope="global")
    assert loaded.input_paths() == (tmp_path / "base.json", optional)
    with pytest.raises(LoadoutError, match="overlaps"):
        render_global(tmp_path)
    assert cli(tmp_path, "sync") == 3
    assert "overlaps" in capsys.readouterr().err
    if present:
        assert optional.read_text() == '{"model":"personal"}'
    else:
        assert not optional.exists()


def test_portable_layers_keep_stricter_rules_and_contributions_from_both_sources(
    tmp_path: Path,
) -> None:
    project(
        tmp_path,
        document_record(
            'permissions = {sources=[{source="shared.toml"},{source="local.toml"}], '
            'renderer="claude-project", keys=["permissions"]}\n'
        ),
    )
    write(tmp_path, "loadout/shared.toml", '[shell]\ndeny=["git push"]\nallow=["git status"]\n')
    write(tmp_path, "loadout/local.toml", '[shell]\nallow=["git push","npm test"]\n')
    result = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(result, str)
    assert json.loads(result) == {
        "permissions": {
            "allow": ["Bash(git status:*)", "Bash(npm test:*)"],
            "deny": ["Bash(git push:*)"],
            "ask": [],
        }
    }


def instruction_record(extra: str = "") -> str:
    return (
        '[[artifact]]\nagents=["claude"]\noutput="CLAUDE.md"\nformat="text"\n'
        'category="instructions"\nmerge="concat"\n'
        'sources=[{source="shared.md"},{source="personal.md",optional=true}]\n' + extra
    )


@pytest.mark.parametrize("mode", [None, 0o644])
def test_composed_instruction_bytes_modes_and_optional_input(
    tmp_path: Path, mode: int | None
) -> None:
    project(tmp_path, instruction_record(f"mode={mode}\n" if mode is not None else ""))
    write(tmp_path, "loadout/shared.md", "\n  Shared\n\nparagraph. \n")
    personal = write(tmp_path, "loadout/personal.md", " \nPersonal.\n")
    write_all(tmp_path)
    output = tmp_path / "CLAUDE.md"
    assert output.read_bytes() == b"Shared\n\nparagraph.\n\nPersonal.\n"
    assert stat.S_IMODE(output.stat().st_mode) == (0o600 if mode is None else mode)
    personal.unlink()
    write_all(tmp_path)
    assert output.read_bytes() == b"Shared\n\nparagraph.\n"
    assert check_all(tmp_path) == []


def test_composed_instruction_empty_activation_and_retirement(tmp_path: Path) -> None:
    index = project(tmp_path, instruction_record())
    base = write(tmp_path, "loadout/shared.md", " \n")
    assert render_project(tmp_path) == {}
    index.write_text(instruction_record("emit_empty=true\n"))
    write_all(tmp_path)
    output = tmp_path / "CLAUDE.md"
    assert output.read_bytes() == b""
    base.write_text("active")
    write_all(tmp_path)
    assert output.read_bytes() == b"active\n"
    index.write_text(instruction_record())
    base.write_text("")
    write_all(tmp_path)
    assert output.exists() is False
    assert check_all(tmp_path) == []


def test_composed_instructions_refuse_invalid_utf8(tmp_path: Path) -> None:
    project(tmp_path, instruction_record())
    write(tmp_path, "loadout/shared.md", b"\xff")
    with pytest.raises(LoadoutError, match=r"UTF-8.*shared.md"):
        render_project(tmp_path)


@pytest.mark.parametrize(
    "part",
    [
        'source="base.json", sources=[{source="local.json"}], merge="deep"',
        'sources=[], merge="deep"',
        'sources=["base.json"], merge="deep"',
        'sources=[{source="base.json"},{source="./base.json"}], merge="deep"',
        'sources=[{source="../escape.json"}], merge="deep"',
        'sources=[{source="base.json"}], optional=true, merge="deep"',
        'sources=[{source="base.json", optional="true"}], merge="deep"',
        'sources=[{source="base.json"}]',
        'source="base.json", merge="deep"',
        'sources=[{source="base.json"}], merge="concat"',
        'sources=[{source="base.json"}], merge="deep", renderer="claude"',
    ],
)
def test_invalid_document_composition_is_rejected(tmp_path: Path, part: str) -> None:
    index = project(tmp_path, document_record(f"settings={{{part}}}\n"))
    with pytest.raises(LoadoutError):
        load_artifacts(index)


@pytest.mark.parametrize("category", ["permissions", "mcp-permissions"])
def test_layered_permissions_require_a_rule_renderer(tmp_path: Path, category: str) -> None:
    index = project(tmp_path, document_record(f'{category}={{{LAYERS}, merge="deep"}}\n'))
    with pytest.raises(LoadoutError, match="portable renderer"):
        load_artifacts(index)


@pytest.mark.parametrize("format_name", ["copy", "tree"])
def test_opaque_routes_retain_a_single_source(tmp_path: Path, format_name: str) -> None:
    index = project(
        tmp_path, instruction_record().replace('format="text"', f'format="{format_name}"')
    )
    with pytest.raises(LoadoutError, match="single source"):
        load_artifacts(index)


def test_layered_input_symlink_is_refused(tmp_path: Path) -> None:
    layered_project(tmp_path)
    write(tmp_path, "loadout/base.json", "{}")
    target = write(tmp_path, "other.json", "{}")
    (tmp_path / "loadout/local.json").symlink_to(target)
    with pytest.raises(LoadoutError, match="symlink"):
        render_project(tmp_path)


@pytest.mark.parametrize("templates", [False, True])
def test_text_permission_layers_merge_before_rendering(tmp_path: Path, templates: bool) -> None:
    project(
        tmp_path,
        '[[artifact]]\nagents=["codex"]\noutput="policy.rules"\nformat="text"\n'
        'category="permissions"\nrenderer="codex-project"\n'
        'sources=[{source="shared.toml"},{source="local.toml",optional=true}]\n',
    )
    write(
        tmp_path,
        "loadout/config.toml",
        'harnesses=["codex"]\npresets=false\nartifacts="artifacts.toml"\n'
        + ('templates=["web"]\n' if templates else ""),
    )
    write(tmp_path, "loadout/shared.toml", '[shell]\nallow=["git status"]\ndeny=["npm test"]\n')
    write(tmp_path, "loadout/local.toml", '[shell]\nallow=["npm test","pytest"]\n')
    if templates:
        write(tmp_path, "loadout/templates/web.toml", 'permissions=["node"]\n')
        write(
            tmp_path,
            "loadout/templates/permissions/node.toml",
            '[shell]\nallow=["npm test","npm run lint"]\n',
        )
    content = render_project(tmp_path)[tmp_path / "policy.rules"]
    extraction = extract_codex(content)
    assert extraction.notes == ()
    assert extraction.rules.deny == ("npm test",)
    assert extraction.rules.allow == (
        ("npm run lint", "git status", "pytest") if templates else ("git status", "pytest")
    )
    write_all(tmp_path)
    assert check_all(tmp_path) == []


def test_catalog_contributions_reach_layered_instructions_and_documents(tmp_path: Path) -> None:
    project(
        tmp_path,
        instruction_record("template_instructions=true\n")
        + document_record(
            f'settings={{{LAYERS}, merge="deep"}}\n'
            'permissions={sources=[{source="shared.toml"},{source="local.toml"}], renderer="claude-project"}\n'
        ),
    )
    write(
        tmp_path,
        "loadout/config.toml",
        'harnesses=["claude"]\npresets=false\nartifacts="artifacts.toml"\ntemplates=["web"]\n',
    )
    write(tmp_path, "loadout/templates/web.toml", 'instructions=["shared"]\npermissions=["node"]\n')
    write(tmp_path, "loadout/templates/instructions/shared.md", "Template advice.\n")
    write(
        tmp_path,
        "loadout/templates/permissions/node.toml",
        '[shell]\nallow=["npm test","npm run lint"]\n',
    )
    body = write(tmp_path, "loadout/shared.md", "Project advice.\n")
    personal = write(tmp_path, "loadout/personal.md", "Personal advice.\n")
    write(tmp_path, "loadout/base.json", '{"model":"base","retained":1}')
    write(tmp_path, "loadout/local.json", '{"model":"own"}')
    write(tmp_path, "loadout/shared.toml", '[shell]\ndeny=["npm test"]\nallow=["git status"]\n')
    write(tmp_path, "loadout/local.toml", '[shell]\nallow=["npm test","pytest"]\n')
    write_all(tmp_path)
    assert (
        tmp_path / "CLAUDE.md"
    ).read_bytes() == b"Template advice.\n\nProject advice.\n\nPersonal advice.\n"
    assert json.loads((tmp_path / ".claude/settings.json").read_text()) == {
        "model": "own",
        "retained": 1,
        "permissions": {
            "allow": ["Bash(npm run lint:*)", "Bash(git status:*)", "Bash(pytest:*)"],
            "deny": ["Bash(npm test:*)"],
            "ask": [],
        },
    }
    body.write_text("")
    personal.unlink()
    write_all(tmp_path)
    assert (tmp_path / "CLAUDE.md").read_bytes() == b"Template advice.\n\n"
    assert check_all(tmp_path) == []


def test_layered_rule_defaults_keep_the_strictest_stated_value(tmp_path: Path) -> None:
    project(
        tmp_path,
        document_record(
            'permissions={sources=[{source="shared.toml"},{source="local.toml"}], renderer="opencode"}\n'
        ),
    )
    write(tmp_path, "loadout/shared.toml", '[shell]\ndefault="ask"\nallow=["git status"]\n')
    write(tmp_path, "loadout/local.toml", '[shell]\ndefault="allow"\ndeny=["git push"]\n')
    result = render_project(tmp_path)[tmp_path / ".claude/settings.json"]
    assert isinstance(result, str)
    bash = json.loads(result)["permission"]["bash"]
    assert bash["*"] == "ask"
    assert bash["git status"] == "allow"
    assert bash["git push"] == "deny"


def test_retirement_cannot_delete_a_new_layer_input(tmp_path: Path) -> None:
    write(tmp_path, "loadout.toml", 'artifacts="artifacts.toml"\n')
    write(
        tmp_path,
        "artifacts.toml",
        '[[artifact]]\nagents=["claude"]\nformat="copy"\ncategory="support"\n'
        f'destination="{tmp_path / "local.json"}"\nsource="seed.json"\n',
    )
    write(tmp_path, "seed.json", '{"model":"personal"}')
    write_all(tmp_path)
    write(
        tmp_path,
        "artifacts.toml",
        '[[artifact]]\nagents=["claude"]\nformat="json"\n'
        f'destination="{tmp_path / "runtime.json"}"\n[artifact.parts]\n'
        f'settings={{{LAYERS}, merge="deep"}}\n',
    )
    write(tmp_path, "base.json", '{"retained":1}')
    with pytest.raises(LoadoutError, match="overlaps source"):
        write_all(tmp_path)
    assert (tmp_path / "local.json").read_text() == '{"model":"personal"}'
