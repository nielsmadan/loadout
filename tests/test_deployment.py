from __future__ import annotations

import json
import shutil
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout import deployment
from loadout.artifacts import Merged, render_artifacts
from loadout.emit import artifact_deployment_scopes, check_all, render_all, write_all
from loadout.errors import LoadoutError
from loadout.native_documents import apply_document, key_fingerprints
from test_artifacts import document_record, project, write
from test_sync_guard import _commit_source


def copy_record(output: str = "CLAUDE.md", source: str = "instructions.md") -> str:
    return (
        '[[artifact]]\nagents = ["claude"]\nformat = "copy"\ncategory = "instructions"\n'
        f'output = "{output}"\nsource = "{source}"\n'
    )


def receipt(root: Path) -> Path:
    return root / "loadout/.loadout-state/project.json"


def setup_copy(root: Path) -> tuple[Path, Path]:
    project(root, copy_record())
    source = write(root, "loadout/instructions.md", b"original\xff\n")
    source.chmod(0o751)
    return source, root / "CLAUDE.md"


def cli(root: Path, command: str, *args: str) -> int:
    return loadout.main([command, "--root", str(root), *args])


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def test_source_changes_sync_against_receipt_and_current_desired_bytes(tmp_path: Path) -> None:
    source, output = setup_copy(tmp_path)
    assert cli(tmp_path, "sync") == 0
    assert output.read_bytes() == b"original\xff\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o751
    source.write_bytes(b"updated\x00\n")
    source.chmod(0o640)
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 0
    assert output.read_bytes() == b"updated\x00\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    source.write_bytes(b"current\n")
    output.write_bytes(b"current\n")
    assert cli(tmp_path, "sync") == 0
    assert check_all(tmp_path) == []


def test_committed_baseline_does_not_override_artifact_receipt(tmp_path: Path) -> None:
    source, output = setup_copy(tmp_path)
    _commit_source(tmp_path)
    assert cli(tmp_path, "sync") == 0
    source.write_bytes(b"working source\n")
    assert cli(tmp_path, "sync") == 0
    source.write_bytes(b"another revision\n")
    assert cli(tmp_path, "sync") == 0
    assert output.read_bytes() == b"another revision\n"


def test_relative_root_uses_artifact_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = setup_copy(tmp_path)
    monkeypatch.chdir(tmp_path)
    write_all(Path("."))
    source.write_bytes(b"new source\n")
    output.write_bytes(b"manual\n")
    assert cli(Path("."), "sync") == 1
    assert output.read_bytes() == b"manual\n"


@pytest.mark.parametrize("edit", ["bytes", "mode"])
def test_manual_output_edits_block_then_force_explicit_target(tmp_path: Path, edit: str) -> None:
    source, output = setup_copy(tmp_path)
    write_all(tmp_path)
    if edit == "bytes":
        output.write_bytes(b"manual\n")
    else:
        output.chmod(0o750)
    source.write_bytes(b"next\n")
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 1
    assert cli(tmp_path, "sync", "--force") == 0
    assert output.read_bytes() == b"next\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o751


def test_new_occupied_path_requires_adoption_without_git_baseline(tmp_path: Path) -> None:
    source, output = setup_copy(tmp_path)
    output.write_bytes(b"existing authored content\n")
    assert cli(tmp_path, "sync") == 1
    assert output.read_bytes() == b"existing authored content\n"
    assert receipt(tmp_path).exists() is False
    assert cli(tmp_path, "sync", "--force") == 0
    assert output.read_bytes() == source.read_bytes()


def test_matching_existing_output_can_be_adopted_and_missing_receipt_is_conservative(
    tmp_path: Path,
) -> None:
    source, output = setup_copy(tmp_path)
    shutil.copy2(source, output)
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 0
    receipt(tmp_path).unlink()
    source.write_bytes(b"changed source\n")
    assert cli(tmp_path, "sync") == 1
    assert output.read_bytes() == b"original\xff\n"


def test_receipt_is_versioned_private_and_gitignored(tmp_path: Path) -> None:
    setup_copy(tmp_path)
    git(tmp_path, "init", "-q")
    write_all(tmp_path)
    path = receipt(tmp_path)
    data = json.loads(path.read_text())
    assert (data["version"], data["scope"], data["pending"]) == (1, "project", [])
    assert data["entries"][0]["route"] == "CLAUDE.md"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert git(tmp_path, "check-ignore", str(path)).strip() == str(path)
    assert git(tmp_path, "status", "--porcelain", "--untracked-files=all").splitlines() == [
        "?? CLAUDE.md",
        "?? loadout/artifacts.toml",
        "?? loadout/config.toml",
        "?? loadout/instructions.md",
    ]


@pytest.mark.parametrize(
    "corruption", ["tracked", "symlink", "version", "invalid", "public", "scope"]
)
def test_bad_receipt_blocks_even_force(tmp_path: Path, corruption: str) -> None:
    source, output = setup_copy(tmp_path)
    write_all(tmp_path)
    path = receipt(tmp_path)
    if corruption == "tracked":
        git(tmp_path, "init", "-q")
        git(tmp_path, "add", "-f", str(path))
    elif corruption == "symlink":
        outside = tmp_path / "saved.json"
        path.rename(outside)
        path.symlink_to(outside)
    elif corruption == "public":
        path.chmod(0o644)
    elif corruption == "invalid":
        path.write_text("{broken")
    else:
        data = json.loads(path.read_text())
        data[corruption] = 99 if corruption == "version" else "global"
        path.write_text(json.dumps(data))
    source.write_bytes(b"changed\n")
    assert cli(tmp_path, "sync", "--force") == 3
    assert output.read_bytes() == b"original\xff\n"


def test_rename_and_last_route_removal_retire_only_receipted_files(tmp_path: Path) -> None:
    source, output = setup_copy(tmp_path)
    write_all(tmp_path)
    write(tmp_path, "loadout/artifacts.toml", copy_record("nested/CLAUDE.md"))
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 0
    renamed = tmp_path / "nested/CLAUDE.md"
    assert renamed.read_bytes() == source.read_bytes()
    assert output.exists() is False
    unrelated = write(tmp_path, "nested/manual.md", "keep\n")
    write(tmp_path, "loadout/artifacts.toml", "")
    assert cli(tmp_path, "sync") == 0
    assert renamed.exists() is False
    assert unrelated.read_text() == "keep\n"
    assert json.loads(receipt(tmp_path).read_text())["entries"] == []
    assert cli(tmp_path, "check") == 0


def test_tree_deletion_and_last_file_cleanup(tmp_path: Path) -> None:
    project(
        tmp_path,
        copy_record(".claude/skills/probe", "skills/probe").replace(
            'format = "copy"', 'format = "tree"'
        ),
    )
    first = write(tmp_path, "loadout/skills/probe/SKILL.md", "skill\n")
    second = write(tmp_path, "loadout/skills/probe/run.sh", "script\n")
    write_all(tmp_path)
    write(tmp_path, ".claude/skills/probe/foreign.txt", "foreign\n")
    first.unlink()
    second.rename(second.with_name("renamed.sh"))
    write_all(tmp_path)
    destination = tmp_path / ".claude/skills/probe"
    assert sorted(p.name for p in destination.iterdir()) == ["foreign.txt", "renamed.sh"]
    second.with_name("renamed.sh").unlink()
    write_all(tmp_path)
    assert sorted(p.name for p in destination.iterdir()) == ["foreign.txt"]
    assert check_all(tmp_path) == []


def test_force_does_not_delete_modified_retirement(tmp_path: Path) -> None:
    _, output = setup_copy(tmp_path)
    write_all(tmp_path)
    output.write_bytes(b"manual\n")
    write(tmp_path, "loadout/artifacts.toml", "")
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync", "--force") == 1
    assert output.read_bytes() == b"manual\n"


@pytest.mark.parametrize("ancestor", [False, True])
@pytest.mark.parametrize("retired", [False, True])
def test_symlinks_never_followed_even_with_force(
    tmp_path: Path, ancestor: bool, retired: bool
) -> None:
    project(tmp_path, copy_record("generated/CLAUDE.md"))
    write(tmp_path, "loadout/instructions.md", "source\n")
    write_all(tmp_path)
    output = tmp_path / "generated/CLAUDE.md"
    target = tmp_path / "outside"
    if ancestor:
        output.parent.rename(target)
        output.parent.symlink_to(target, target_is_directory=True)
        external = target / "CLAUDE.md"
    else:
        output.rename(target)
        output.symlink_to(target)
        external = target
    if retired:
        write(tmp_path, "loadout/artifacts.toml", "")
    assert cli(tmp_path, "sync", "--force") == 3
    assert external.read_text() == "source\n"


def test_moving_checkout_preserves_detached_old_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / "old"
    setup_copy(old)
    write_all(old)
    moved = tmp_path / "moved"
    shutil.copytree(old, moved)
    write(moved, "loadout/artifacts.toml", "")
    write_all(moved)
    assert (old / "CLAUDE.md").read_bytes() == b"original\xff\n"
    assert (moved / "CLAUDE.md").read_bytes() == b"original\xff\n"
    assert (
        f"detached deployment retained for explicit cleanup: {old / 'CLAUDE.md'}"
        in capsys.readouterr().out
    )


def global_partial(
    root: Path, destination: str, *, format_name: str = "json", extra: str = ""
) -> Path:
    write(root, "loadout.toml", 'artifacts = "artifacts.toml"\n')
    write(
        root,
        "artifacts.toml",
        (
            '[[artifact]]\nagents = ["pi"]\n'
            f'destination = "{destination}"\nformat = "{format_name}"\npartial = true\n{extra}'
            '[artifact.parts]\nsettings = {source = "settings.' + format_name + '"}\n'
        ),
    )
    return root / f"settings.{format_name}"


def test_json_partial_preserves_runtime_and_removes_authored_keys(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/.pi/agent/settings.json")
    source.write_text('{"model": "first", "enabled": false, "data": null}')
    output = write(fake_home, ".pi/agent/settings.json", '{"lastChangelogVersion":"old"}')
    output.chmod(0o640)
    assert cli(tmp_path, "sync") == 0
    data = json.loads(output.read_text())
    assert data == {"lastChangelogVersion": "old", "model": "first", "enabled": False, "data": None}
    data["lastChangelogVersion"] = "new"
    output.write_text(json.dumps(data))
    assert cli(tmp_path, "check") == 0
    source.write_text('{"model":"second"}')
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"lastChangelogVersion": "new", "model": "second"}
    source.write_text("{}")
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"lastChangelogVersion": "new"}
    assert stat.S_IMODE(output.stat().st_mode) == 0o640
    assert cli(tmp_path, "check") == 0


def test_partial_manual_owned_changes_block_but_force_preserves_foreign(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "${CLAUDE_CONFIG_DIR:-~}/.claude.json")
    source.write_text('{"mcpServers":{"search":{"command":"first"}}}')
    write_all(tmp_path)
    output = fake_home / ".claude.json"
    output.write_text('{"mcpServers": {}, "projects": {"local": true}}')
    assert cli(tmp_path, "sync") == 1
    assert cli(tmp_path, "sync", "--force") == 0
    assert json.loads(output.read_text()) == {
        "projects": {"local": True},
        "mcpServers": {"search": {"command": "first"}},
    }


@pytest.mark.parametrize("format_name", ["json", "toml"])
def test_empty_partial_source_stays_absent_then_activates(
    tmp_path: Path, fake_home: Path, format_name: str
) -> None:
    source = global_partial(tmp_path, f"~/runtime.{format_name}", format_name=format_name)
    source.write_text("{}" if format_name == "json" else "")
    output = fake_home / f"runtime.{format_name}"
    assert cli(tmp_path, "sync") == 0
    assert output.exists() is False
    assert cli(tmp_path, "check") == 0
    source.write_text('{"model":"x"}' if format_name == "json" else 'model = "x"\n')
    assert cli(tmp_path, "sync") == 0
    assert output.is_file()
    assert cli(tmp_path, "check") == 0


@pytest.mark.parametrize("format_name", ["json", "toml"])
def test_emit_empty_partial_explicitly_creates_runtime_file(
    tmp_path: Path, fake_home: Path, format_name: str
) -> None:
    source = global_partial(
        tmp_path, f"~/runtime.{format_name}", format_name=format_name, extra="emit_empty = true\n"
    )
    source.write_text("{}" if format_name == "json" else "")
    write_all(tmp_path)
    output = fake_home / f"runtime.{format_name}"
    assert output.is_file()
    assert output.read_text() == ("{}\n" if format_name == "json" else "")
    assert check_all(tmp_path) == []


@pytest.mark.parametrize("reserved", [".git", ".loadout-state"])
@pytest.mark.parametrize("route_kind", ["source", "output", "tree"])
def test_artifacts_cannot_consume_or_write_metadata(
    tmp_path: Path, reserved: str, route_kind: str
) -> None:
    if route_kind == "tree":
        project(
            tmp_path,
            copy_record("generated", "support").replace('format = "copy"', 'format = "tree"'),
        )
        write(tmp_path, f"loadout/support/{reserved}/private", "private\n")
    elif route_kind == "source":
        project(tmp_path, copy_record(source=f"{reserved}/private"))
        write(tmp_path, f"loadout/{reserved}/private", "private\n")
    else:
        project(tmp_path, copy_record(output=f"{reserved}/private"))
        write(tmp_path, "loadout/instructions.md", "source\n")
    with pytest.raises(LoadoutError, match="protected metadata"):
        render_all(tmp_path)


def test_deleted_artifact_reference_still_retires_previous_outputs(tmp_path: Path) -> None:
    _, output = setup_copy(tmp_path)
    write_all(tmp_path)
    config = tmp_path / "loadout/config.toml"
    config.write_text('harnesses = ["claude"]\npresets = false\n')
    assert cli(tmp_path, "check") == 1
    write_all(tmp_path)
    assert output.exists() is False
    assert check_all(tmp_path) == []


def test_optional_partial_source_removal_retires_last_owned_key(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    index = tmp_path / "artifacts.toml"
    index.write_text(
        index.read_text().replace(
            'source = "settings.json"', 'source = "settings.json", optional = true'
        )
    )
    source.write_text('{"model":"x"}')
    output = write(fake_home, "runtime.json", '{"runtime": 42}')
    write_all(tmp_path)
    source.unlink()
    write_all(tmp_path)
    assert json.loads(output.read_text()) == {"runtime": 42}
    assert check_all(tmp_path) == []


def test_partial_first_sync_refuses_occupied_owned_keys(tmp_path: Path, fake_home: Path) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    source.write_text('{"model":"source"}')
    output = write(fake_home, "runtime.json", '{"model":"manual","runtime":42}')
    assert cli(tmp_path, "sync") == 1
    assert json.loads(output.read_text()) == {"model": "manual", "runtime": 42}


def test_partial_new_owned_key_must_be_absent_or_match_source(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    source.write_text('{"model":"first"}')
    output = write(fake_home, "runtime.json", '{"runtime":42}')
    write_all(tmp_path)
    source.write_text('{"model":"second","runtime":99}')
    assert cli(tmp_path, "sync") == 1
    source.write_text('{"model":"second","runtime":42}')
    assert cli(tmp_path, "sync") == 0
    assert json.loads(output.read_text()) == {"model": "second", "runtime": 42}


TOML_FOREIGN = '''# foreign configuration
"literal.dot" = """
[model]
"quoted key" = "fake assignment inside a string"
"""
foreign_array = [
    {name = "one", value = 1}, # untouched
    {name = "two", value = 2},
]

[projects."/Users/me/a.b"] # preserve this header
trust_level = 'trusted'

[[history]]
path = "/Users/me/a.b"
'''


def test_partial_toml_preserves_exact_foreign_bytes_through_changes_and_removal(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/.codex/config.toml", format_name="toml")
    source.write_text(
        '"quoted key" = """first\nsecond"""\nmodel = "first"\n[mcp_servers.search]\ncommand="uvx"\n'
    )
    output = write(fake_home, ".codex/config.toml", TOML_FOREIGN)
    write_all(tmp_path)
    first = output.read_text()
    assert tomllib.loads(first)["quoted key"] == "first\nsecond"
    owned = frozenset({"quoted key", "model", "mcp_servers"})
    assert apply_document(first, owned, "", "toml") == TOML_FOREIGN
    source.write_text(
        '"quoted key" = [1, 2]\nmodel = "second"\n[mcp_servers.other]\nargs=[{x=1}]\n'
    )
    write_all(tmp_path)
    second = output.read_text()
    assert apply_document(second, owned, "", "toml") == TOML_FOREIGN
    assert tomllib.loads(second)["mcp_servers"] == {"other": {"args": [{"x": 1}]}}
    assert cli(tmp_path, "check") == 0
    source.write_text("")
    write_all(tmp_path)
    assert output.read_text() == TOML_FOREIGN
    assert cli(tmp_path, "check") == 0


@pytest.mark.parametrize("format_name", ["json", "toml"])
@pytest.mark.parametrize("force", [False, True])
def test_partial_explicit_order_changes_sync_and_preserve_runtime(
    tmp_path: Path, fake_home: Path, format_name: str, force: bool
) -> None:
    source = global_partial(
        tmp_path,
        f"~/runtime.{format_name}",
        format_name=format_name,
        extra='order = ["first", "second"]\n',
    )
    source.write_text(
        '{"first":{"nested":1,"other":2},"second":false}'
        if format_name == "json"
        else "first = { nested = 1, other = 2 }\nsecond = false\n"
    )
    foreign = '{"runtime":{"nested":2,"first":1}}' if format_name == "json" else TOML_FOREIGN
    output = write(fake_home, f"runtime.{format_name}", foreign)
    parse = json.loads if format_name == "json" else tomllib.loads
    owned = frozenset({"first", "second"})
    assert cli(tmp_path, "sync") == 0
    assert [key for key in parse(output.read_text()) if key in owned] == ["first", "second"]
    index = tmp_path / "artifacts.toml"
    index.write_text(index.read_text().replace('["first", "second"]', '["second", "first"]'))
    rendered = render_all(tmp_path)[output]
    assert isinstance(rendered, Merged)
    assert list(parse(rendered.document)) == ["second", "first"]
    assert output in {path for path, _, _ in check_all(tmp_path)}
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync", *(("--force",) if force else ())) == 0
    result = parse(output.read_text())
    assert [key for key in result if key in owned] == ["second", "first"]
    assert list(result["first"]) == ["nested", "other"]
    if format_name == "toml":
        assert apply_document(output.read_text(), owned, "", format_name) == foreign
        runtime_change = output.read_text().replace(
            "trust_level = 'trusted'", "trust_level = 'new'"
        )
    else:
        assert list(result["runtime"].items()) == [("nested", 2), ("first", 1)]
        runtime_change = output.read_text().replace('"nested": 2', '"nested": 3')
    assert runtime_change != output.read_text()
    output.write_text(runtime_change)
    assert check_all(tmp_path) == []
    assert cli(tmp_path, "sync") == 0
    assert output.read_text() == runtime_change


@pytest.mark.parametrize("format_name", ["json", "toml"])
@pytest.mark.parametrize("retire", [False, True])
def test_partial_manual_owned_order_changes_block_sync_and_retirement(
    tmp_path: Path, fake_home: Path, format_name: str, retire: bool
) -> None:
    source = global_partial(tmp_path, f"~/runtime.{format_name}", format_name=format_name)
    source.write_text(
        '{"first":1,"second":2,"third":3}'
        if format_name == "json"
        else "first = 1\nsecond = 2\nthird = 3\n"
    )
    assert cli(tmp_path, "sync") == 0
    output = fake_home / f"runtime.{format_name}"
    manual = (
        '{"second":2,"first":1,"third":3,"runtime":42}'
        if format_name == "json"
        else "second = 2\nfirst = 1\nthird = 3\n" + TOML_FOREIGN
    )
    output.write_text(manual)
    state = tmp_path / ".loadout-state/global.json"
    before_receipt = state.read_bytes()
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 1
    assert output.read_text() == manual
    assert state.read_bytes() == before_receipt
    index = tmp_path / "artifacts.toml"
    index.write_text(
        ""
        if retire
        else index.read_text().replace(
            "partial = true", 'partial = true\norder = ["third", "second", "first"]'
        )
    )
    assert cli(tmp_path, "sync") == 1
    assert output.read_text() == manual
    if retire:
        assert cli(tmp_path, "sync", "--force") == 1
        assert output.read_text() == manual
    else:
        assert cli(tmp_path, "sync", "--force") == 0
        parse = json.loads if format_name == "json" else tomllib.loads
        assert [
            key for key in parse(output.read_text()) if key in {"first", "second", "third"}
        ] == ["third", "second", "first"]
        assert check_all(tmp_path) == []


def test_toml_syntax_aware_removal_handles_dotted_and_array_tables() -> None:
    owned_text = '''"quoted.key" = """multiline
[foreign_fake]
"""
root.child.value = 1
[[array_owned]]
value = 1
[[array_owned]]
value = 2
'''
    existing = TOML_FOREIGN.replace("[projects.", owned_text + "\n[projects.", 1)
    result = apply_document(existing, frozenset({"quoted.key", "root", "array_owned"}), "", "toml")
    assert tomllib.loads(result) == tomllib.loads(TOML_FOREIGN)
    assert result == TOML_FOREIGN


@pytest.mark.parametrize(
    "literal", ["1979-05-27", "07:32:00", "1979-05-27T07:32:00Z", "nan", "+inf", "-inf"]
)
def test_toml_fingerprints_preserve_types_and_nonfinite_values(literal: str) -> None:
    native = f"value = {literal}\n"
    string = f'value = "{literal}"\n'
    owned = frozenset({"value"})
    assert key_fingerprints(native, "toml", owned) != key_fingerprints(string, "toml", owned)
    changed = apply_document(native, owned, string, "toml")
    assert tomllib.loads(changed)["value"] == literal
    assert apply_document(native, owned, native, "toml") == native


def test_nested_toml_temporal_value_becoming_string_is_applied() -> None:
    old = "value = { dates = [1979-05-27] }\n"
    new = 'value = { dates = ["1979-05-27"] }\n'
    result = apply_document(old, frozenset({"value"}), new, "toml")
    assert tomllib.loads(result) == {"value": {"dates": ["1979-05-27"]}}


def test_partial_render_is_pure_and_uses_existing_merged_type(tmp_path: Path) -> None:
    project(
        tmp_path, document_record('settings = {source = "settings.json"}', extra="partial = true\n")
    )
    write(tmp_path, "loadout/settings.json", '{"model":"x"}')
    first = render_all(tmp_path)
    write(tmp_path, ".claude/settings.json", "invalid runtime JSON")
    second = render_all(tmp_path)
    assert first == second
    assert isinstance(next(iter(first.values())), Merged)
    scopes = artifact_deployment_scopes(tmp_path)
    assert scopes[0].artifacts is not None
    assert render_artifacts(scopes[0].artifacts, project_root=tmp_path) == first


def test_partial_route_removal_strips_ownership_and_keeps_runtime(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    source.write_text('{"model":"x"}')
    output = write(fake_home, "runtime.json", '{"runtime":42}')
    write_all(tmp_path)
    (tmp_path / "artifacts.toml").write_text("")
    assert cli(tmp_path, "check") == 1
    write_all(tmp_path)
    assert json.loads(output.read_text()) == {"runtime": 42}
    assert cli(tmp_path, "check") == 0


def test_relocated_global_root_keeps_old_deployment(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = global_partial(tmp_path, "${CODEX_HOME:-~/.codex}/config.json")
    source.write_text('{"model":"x"}')
    write_all(tmp_path)
    old = fake_home / ".codex/config.json"
    relocated = fake_home / "relocated"
    monkeypatch.setenv("CODEX_HOME", str(relocated))
    write_all(tmp_path)
    assert json.loads(old.read_text()) == {"model": "x"}
    assert json.loads((relocated / "config.json").read_text()) == {"model": "x"}
    assert f"detached deployment retained for explicit cleanup: {old}" in capsys.readouterr().out
    assert check_all(tmp_path) == []


def test_receipts_stay_scope_local_and_global_removal_keeps_project(
    tmp_path: Path, fake_home: Path
) -> None:
    source, project_output = setup_copy(tmp_path)
    global_source = global_partial(tmp_path, "~/global.json")
    global_source.write_text('{"model":"x"}')
    write_all(tmp_path)
    (tmp_path / "artifacts.toml").write_text("")
    write_all(tmp_path)
    assert project_output.read_bytes() == source.read_bytes()
    assert json.loads((fake_home / "global.json").read_text()) == {}
    assert json.loads(receipt(tmp_path).read_text())["scope"] == "project"
    assert json.loads((tmp_path / ".loadout-state/global.json").read_text())["entries"] == []


def test_removed_receipt_owner_cannot_collide_with_new_scope_output(tmp_path: Path) -> None:
    _, output = setup_copy(tmp_path)
    write_all(tmp_path)
    (tmp_path / "loadout/artifacts.toml").write_text("")
    write(tmp_path, "loadout.toml", 'artifacts = "artifacts.toml"\n')
    write(
        tmp_path,
        "artifacts.toml",
        copy_record().replace('output = "CLAUDE.md"', f'destination = "{output}"'),
    )
    write(tmp_path, "instructions.md", "global\n")
    assert cli(tmp_path, "sync", "--force") == 3
    assert output.read_bytes() == b"original\xff\n"


def test_profile_switch_uses_previous_deployment_without_committed_source(
    tmp_path: Path, fake_home: Path
) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    source.write_text('{"model":"default"}')
    alternate = (
        (tmp_path / "artifacts.toml")
        .read_text()
        .replace('source = "settings.json"', 'source = "other.json"')
    )
    write(tmp_path, "other-artifacts.toml", alternate)
    write(tmp_path, "other.json", '{"model":"other"}')
    write(
        tmp_path,
        "other.toml",
        'extends = "default"\nartifacts = "other-artifacts.toml"\n',
    )
    write_all(tmp_path)
    assert cli(tmp_path, "sync", "--profile", "other") == 0
    assert json.loads((fake_home / "runtime.json").read_text()) == {"model": "other"}
    write_all(tmp_path)
    assert json.loads((fake_home / "runtime.json").read_text()) == {"model": "default"}


def test_frozen_plan_copies_original_bytes_and_mode(tmp_path: Path) -> None:
    source, output = setup_copy(tmp_path)
    plan = deployment.prepare_deployment(artifact_deployment_scopes(tmp_path), render_all(tmp_path))
    source.write_bytes(b"later\n")
    source.chmod(0o600)
    deployment.apply_deployment(plan)
    assert output.read_bytes() == b"original\xff\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o751
    write_all(tmp_path)
    assert output.read_bytes() == b"later\n"


@pytest.mark.parametrize("target", ["output", "receipt", "parent", "unchanged"])
def test_plan_rechecks_destinations_and_receipts_before_mutation(
    tmp_path: Path, target: str
) -> None:
    source, output = setup_copy(tmp_path)
    write_all(tmp_path)
    if target != "unchanged":
        source.write_bytes(b"next\n")
    plan = deployment.prepare_deployment(artifact_deployment_scopes(tmp_path), render_all(tmp_path))
    if target in {"output", "unchanged"}:
        output.write_bytes(b"manual\n")
    elif target == "receipt":
        receipt(tmp_path).write_text("{}")
    else:
        output.unlink()
        output.symlink_to(source)
    with pytest.raises(LoadoutError):
        deployment.apply_deployment(plan)
    assert source.read_bytes() == (b"original\xff\n" if target == "unchanged" else b"next\n")


def test_interrupted_sync_resumes_then_accepts_a_new_source_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, output = setup_copy(tmp_path)
    project(tmp_path, copy_record() + copy_record("second.md", "second.md"))
    second_source = write(tmp_path, "loadout/second.md", "second\n")
    original = deployment.atomic_install

    def interrupted(path: Path, frozen: deployment.FrozenFile) -> None:
        if path == tmp_path / "second.md":
            raise OSError("simulated interruption")
        original(path, frozen)

    monkeypatch.setattr(deployment, "atomic_install", interrupted)
    with pytest.raises(OSError, match="simulated interruption"):
        write_all(tmp_path)
    assert output.read_bytes() == source.read_bytes()
    assert len(json.loads(receipt(tmp_path).read_text())["pending"]) == 2
    source.write_bytes(b"third revision\n")
    monkeypatch.setattr(deployment, "atomic_install", original)
    write_all(tmp_path)
    assert output.read_bytes() == b"third revision\n"
    assert (tmp_path / "second.md").read_bytes() == second_source.read_bytes()
    assert check_all(tmp_path) == []


@pytest.mark.parametrize("installed", [False, True])
@pytest.mark.parametrize("external_edit", [False, True])
def test_interrupted_ownership_expansion_retirement_guards_every_removed_key(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    installed: bool,
    external_edit: bool,
) -> None:
    source = global_partial(tmp_path, "~/runtime.json")
    source.write_text('{"model":"initial"}')
    output = write(fake_home, "runtime.json", '{"session":true}')
    write_all(tmp_path)
    source.write_text('{"model":"updated","runtime":42}')
    original = deployment.atomic_install

    def interrupted(path: Path, frozen: deployment.FrozenFile) -> None:
        if path == output:
            if installed:
                original(path, frozen)
            raise OSError("simulated ownership expansion interruption")
        original(path, frozen)

    with monkeypatch.context() as patch:
        patch.setattr(deployment, "atomic_install", interrupted)
        with pytest.raises(OSError, match="simulated ownership expansion interruption"):
            write_all(tmp_path)
    state = tmp_path / ".loadout-state/global.json"
    before_receipt = state.read_bytes()
    saved = json.loads(before_receipt)
    assert saved["entries"][0]["owned"] == ["model"]
    assert saved["pending"][0]["owned"] == ["model", "runtime"]
    data = json.loads(output.read_text())
    assert data == (
        {"session": True, "model": "updated", "runtime": 42}
        if installed
        else {"session": True, "model": "initial"}
    )
    if external_edit:
        data["runtime"] = 99
        output.write_text(json.dumps(data))
    (tmp_path / "artifacts.toml").write_text("")
    assert cli(tmp_path, "check") == 1
    if external_edit:
        assert cli(tmp_path, "sync") == 1
        assert json.loads(output.read_text()) == data
        assert state.read_bytes() == before_receipt
        assert cli(tmp_path, "sync", "--force") == 1
        assert json.loads(output.read_text()) == data
    else:
        assert cli(tmp_path, "sync") == 0
        assert json.loads(output.read_text()) == {"session": True}
        assert check_all(tmp_path) == []


@pytest.mark.parametrize("format_name", ["json", "toml"])
def test_interrupted_partial_order_guards_previous_and_pending_ownership(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, format_name: str
) -> None:
    source = global_partial(tmp_path, f"~/runtime.{format_name}", format_name=format_name)
    source.write_text(
        '{"first":1,"second":2}' if format_name == "json" else "first = 1\nsecond = 2\n"
    )
    write_all(tmp_path)
    source.write_text(
        '{"first":1,"second":2,"third":3}'
        if format_name == "json"
        else "first = 1\nsecond = 2\nthird = 3\n"
    )
    output = fake_home / f"runtime.{format_name}"
    original = deployment.atomic_install

    def interrupted(path: Path, frozen: deployment.FrozenFile) -> None:
        if path == output:
            raise OSError("simulated ownership expansion interruption")
        original(path, frozen)

    with monkeypatch.context() as patch:
        patch.setattr(deployment, "atomic_install", interrupted)
        with pytest.raises(OSError, match="simulated ownership expansion interruption"):
            write_all(tmp_path)
    state = tmp_path / ".loadout-state/global.json"
    before_receipt = state.read_bytes()
    saved = json.loads(before_receipt)
    assert saved["entries"][0]["owned"] == ["first", "second"]
    assert saved["pending"][0]["owned"] == ["first", "second", "third"]
    manual = (
        '{"first":1,"third":3,"second":2}'
        if format_name == "json"
        else "first = 1\nthird = 3\nsecond = 2\n"
    )
    output.write_text(manual)
    assert cli(tmp_path, "check") == 1
    assert cli(tmp_path, "sync") == 1
    assert output.read_text() == manual
    assert state.read_bytes() == before_receipt
    index = tmp_path / "artifacts.toml"
    index.write_text(
        index.read_text().replace(
            "partial = true", 'partial = true\norder = ["first", "third", "second"]'
        )
    )
    assert cli(tmp_path, "sync") == 0
    assert output.read_text() == manual
    assert check_all(tmp_path) == []


@pytest.mark.parametrize("partial", [False, True])
def test_interrupted_retirement_can_finish(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, partial: bool
) -> None:
    if partial:
        source = global_partial(tmp_path, "~/runtime.json")
        source.write_text('{"model":"x"}')
        output = write(fake_home, "runtime.json", '{"runtime":42}')
        index = tmp_path / "artifacts.toml"
        state = tmp_path / ".loadout-state/global.json"
    else:
        _, output = setup_copy(tmp_path)
        index = tmp_path / "loadout/artifacts.toml"
        state = receipt(tmp_path)
    write_all(tmp_path)
    index.write_text("")
    original = deployment.atomic_install

    def interrupted(path: Path, frozen: deployment.FrozenFile) -> None:
        if path == state and json.loads(frozen.content)["entries"] == []:
            raise OSError("simulated receipt interruption")
        original(path, frozen)

    monkeypatch.setattr(deployment, "atomic_install", interrupted)
    with pytest.raises(OSError, match="simulated receipt interruption"):
        write_all(tmp_path)
    monkeypatch.setattr(deployment, "atomic_install", original)
    write_all(tmp_path)
    if partial:
        assert json.loads(output.read_text()) == {"runtime": 42}
    else:
        assert output.exists() is False
    assert check_all(tmp_path) == []
