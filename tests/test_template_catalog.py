from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomlkit

from loadout import template_vendor
from loadout.cli import main
from loadout.emit import render_global, render_project
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.project import load_project_config, project_config_path
from loadout.template_catalog import load_catalog
from loadout.templates import resolve_template, template_divergence, tree_hash, vendored_path

pytestmark = pytest.mark.integration


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest.fixture
def catalog(fake_home: Path) -> Path:
    source = fake_home / "ac"
    catalog = source / "loadout/templates"
    _write(
        source / "loadout.toml",
        '[[source]]\nname = "ac"\npath = "loadout"\nuse = ["templates"]\n[claude]\ninstructions = []\n',
    )
    _write(machine_config_path(), f'source = "{source}"\n')
    _write(
        catalog / "nextjs.toml",
        'skills = ["review-typescript"]\ninstructions = ["typescript", "nextjs"]\nmcp = ["github"]\npermissions = ["node"]\n',
    )
    _write(
        catalog / "react-native.toml",
        'skills = ["review-typescript"]\ninstructions = ["typescript"]\nmcp = ["github"]\n',
    )
    _write(
        catalog / "skills/review-typescript/SKILL.md",
        "---\nname: review-typescript\ndescription: Review types.\n---\nReview TypeScript.\n",
    )
    _write(catalog / "instructions/typescript.md", "Shared TypeScript advice\n")
    _write(catalog / "instructions/nextjs.md", "Next.js advice\n")
    _write(catalog / "mcp/github.toml", '[github]\ntransport = "stdio"\ncommand = "github-mcp"\n')
    _write(catalog / "permissions/node.toml", '[shell]\nallow = ["npm test"]\n')
    return catalog


def _native(root: Path, agents: tuple[str, ...] = ("claude",)) -> None:
    source = root / "loadout"
    source.mkdir(parents=True, exist_ok=True)
    (source / "config.toml").write_text(tomlkit.dumps({"harnesses": list(agents)}))
    (source / "permissions.toml").write_text("")


def _answering(monkeypatch: pytest.MonkeyPatch, answer: object) -> None:
    """A terminal that answers the shared-parts prompt with `answer`."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", answer)


def _template(root: Path, action: str, name: str, *extra: str) -> int:
    if action == "vendored":
        return main(["template", "add", name, "--vendored", "--root", str(root)])
    if action == "tracked":
        return main(["template", "add", name, "--tracked", "--root", str(root)])
    return main(["template", action, name, "--root", str(root), *extra])


def test_two_templates_share_one_vendored_part_and_render_once(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    local = bare_project / "loadout/templates"
    assert list(local.rglob("SKILL.md")) == [local / "skills/review-typescript/SKILL.md"]
    machine_config_path().unlink()
    output = render_project(bare_project)
    instructions = output[bare_project / "AGENTS.md"]
    assert isinstance(instructions, str)
    assert instructions.count("Shared TypeScript advice") == 1
    assert "Next.js advice" in instructions
    assert (
        json.loads(output[bare_project / ".mcp.json"])["mcpServers"]["github"]["command"]
        == "github-mcp"
    )
    assert template_divergence(bare_project) == []


def test_shared_update_prompts_for_all_affected_templates(
    bare_project: Path,
    catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    path = catalog / "instructions/typescript.md"
    path.write_text("Updated shared advice\n")
    before = project_config_path(bare_project).read_bytes()
    _answering(monkeypatch, lambda _: "no")
    assert _template(bare_project, "sync", "nextjs") == 0
    assert project_config_path(bare_project).read_bytes() == before
    out = capsys.readouterr().out
    assert "Affected templates: nextjs, react-native" in out
    assert "declined; no changes made" in out
    _answering(monkeypatch, lambda _: "yes")
    assert _template(bare_project, "sync", "nextjs") == 0
    assert (
        bare_project / "loadout/templates/instructions/typescript.md"
    ).read_text() == "Updated shared advice\n"
    assert template_divergence(bare_project) == []
    config = load_project_config(project_config_path(bare_project))
    assert config.vendored_hash("react-native") == tree_hash(
        vendored_path(bare_project, "react-native")
    )


def test_shared_update_refuses_local_edits_in_other_affected_template(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    local = bare_project / "loadout/templates"
    (local / "react-native.toml").write_text('instructions = ["typescript"]\n')
    (catalog / "instructions/typescript.md").write_text("upstream\n")
    assert _template(bare_project, "sync", "nextjs") == 3
    assert (local / "instructions/typescript.md").read_text() == "Shared TypeScript advice\n"


@pytest.mark.parametrize("reference", ("../skills/global", "/tmp/skill", "nested/skill", ".."))
def test_references_cannot_escape_catalog(catalog: Path, reference: str) -> None:
    path = _write(catalog / "invalid.toml", f"skills = [{json.dumps(reference)}]\n")
    with pytest.raises(LoadoutError, match="catalog names"):
        load_catalog(path)


def test_qualified_catalog_name_vendors_valid_toml(bare_project: Path, catalog: Path) -> None:
    assert _template(bare_project, "vendored", "ac/nextjs") == 0
    config = load_project_config(project_config_path(bare_project))
    assert config.templates == ("ac/nextjs",)
    assert (
        resolve_template("ac/nextjs", bare_project).path
        == bare_project / "loadout/templates/ac/nextjs.toml"
    )


def test_commented_template_list_remains_valid(bare_project: Path, catalog: Path) -> None:
    path = project_config_path(bare_project)
    path.write_text(path.read_text() + '"templates" = [] # keep this list\n')
    assert _template(bare_project, "tracked", "nextjs") == 0
    assert load_project_config(path).templates == ("nextjs",)
    assert "# keep this list" in path.read_text()


def test_catalog_parts_do_not_activate_global_outputs(catalog: Path) -> None:
    root = catalog.parent.parent
    _write(
        root / "loadout.toml",
        '[[source]]\nname = "ac"\npath = "loadout"\n[claude]\ninstructions = []\n',
    )
    _write(catalog.parent / "permissions.toml", "")
    _write(catalog.parent / "skills/active/SKILL.md", "---\nname: active\n---\nActive skill\n")
    parked = catalog.parents[2] / "parked-catalog"
    catalog.rename(parked)
    before = render_global(root)
    assert "Active skill" in before[catalog.parents[2] / ".claude/skills/active/SKILL.md"]
    parked.rename(catalog)
    assert render_global(root) == before
    _write(catalog / "skills/review-typescript/SKILL.md", "Changed referenced skill")
    _write(catalog / "instructions/typescript.md", "Changed referenced instructions")
    _write(catalog / "mcp/github.toml", '[github]\ntransport="stdio"\ncommand="changed"\n')
    _write(catalog / "permissions/node.toml", '[shell]\ndeny=["npm test"]\n')
    _write(catalog / "skills/unused/SKILL.md", "unused skill")
    _write(catalog / "mcp/unused.toml", "not even valid TOML")
    assert render_global(root) == before
    assert load_catalog(catalog / "nextjs.toml").skills()[0].name == "review-typescript"


def test_adding_second_template_refuses_different_shared_bytes(
    bare_project: Path, catalog: Path
) -> None:
    assert _template(bare_project, "vendored", "nextjs") == 0
    before = project_config_path(bare_project).read_bytes()
    (catalog / "instructions/typescript.md").write_text("new upstream")
    assert _template(bare_project, "vendored", "react-native") == 3
    assert project_config_path(bare_project).read_bytes() == before


def test_shared_skill_sync_removes_retired_support_file(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    support = _write(catalog / "skills/review-typescript/scripts/check", "old script")
    support.chmod(0o755)
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    local = bare_project / "loadout/templates/skills/review-typescript/scripts/check"
    assert local.stat().st_mode & 0o777 == 0o755
    support.unlink()
    _answering(monkeypatch, lambda _: "yes")
    assert _template(bare_project, "sync", "nextjs") == 0
    assert not local.exists()
    assert template_divergence(bare_project) == []
    assert tree_hash(vendored_path(bare_project, "nextjs")) == tree_hash(catalog / "nextjs.toml")


def test_removing_reference_keeps_part_needed_by_other_template(
    bare_project: Path, catalog: Path
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    (catalog / "nextjs.toml").write_text('instructions = ["nextjs"]\n')
    assert _template(bare_project, "sync", "nextjs") == 0
    assert (
        bare_project / "loadout/templates/instructions/typescript.md"
    ).read_text() == "Shared TypeScript advice\n"
    assert template_divergence(bare_project) == []


def test_new_local_skill_file_during_confirmation_refuses_update(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    (catalog / "instructions/typescript.md").write_text("new upstream")
    before = project_config_path(bare_project).read_bytes()

    def answer(_: str) -> str:
        _write(bare_project / "loadout/templates/skills/review-typescript/new.txt", "user edit")
        return "yes"

    _answering(monkeypatch, answer)
    assert _template(bare_project, "sync", "nextjs") == 3
    assert project_config_path(bare_project).read_bytes() == before


def test_catalog_rejects_symlink_to_active_skill(catalog: Path) -> None:
    active = _write(catalog.parent / "skills/active/SKILL.md", "active")
    (catalog / "skills/link").symlink_to(active.parent)
    _write(catalog / "unsafe.toml", 'skills = ["link"]\n')
    with pytest.raises(LoadoutError, match="symlink"):
        load_catalog(catalog / "unsafe.toml")


def test_manifest_and_directory_same_name_are_ambiguous(tmp_path: Path, catalog: Path) -> None:
    (catalog / "nextjs").mkdir()
    with pytest.raises(LoadoutError, match="ambiguous"):
        resolve_template("nextjs", tmp_path)


def test_undeclared_catalog_sync_cannot_bypass_provenance(
    bare_project: Path, catalog: Path
) -> None:
    local = bare_project / "loadout/templates/nextjs.toml"
    _write(local, 'instructions = ["mine"]\n')
    own = _write(local.parent / "instructions/mine.md", "keep my local advice")
    before = project_config_path(bare_project).read_bytes()
    assert _template(bare_project, "sync", "nextjs") == 3
    assert own.read_text() == "keep my local advice"
    assert project_config_path(bare_project).read_bytes() == before


def test_shared_update_rolls_back_a_write_failure(
    bare_project: Path, catalog: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = _write(catalog / "skills/review-typescript/retired.sh", "retired script")
    retired.chmod(0o750)
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    local = bare_project / "loadout/templates"

    def snapshot() -> dict[Path, tuple[bytes, int]]:
        return {
            p.relative_to(local): (p.read_bytes(), p.stat().st_mode & 0o7777)
            for p in local.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    original_config = project_config_path(bare_project).read_bytes()
    (catalog / "instructions/typescript.md").write_text("changed")
    retired.unlink()
    added = _write(catalog / "skills/review-typescript/added.sh", "new script")
    added.chmod(0o755)
    _answering(monkeypatch, lambda _: "yes")
    install = template_vendor.atomic_install

    def fail(path: Path, content: template_vendor.FrozenFile) -> None:
        if path == project_config_path(bare_project) and content.content != original_config:
            raise OSError("injected write failure")
        install(path, content)

    monkeypatch.setattr(template_vendor, "atomic_install", fail)
    assert _template(bare_project, "sync", "nextjs") == 3
    assert snapshot() == before
    assert project_config_path(bare_project).read_bytes() == original_config


def _part_source(root: Path, category: str) -> Path:
    return root / "loadout" / ("mcp.toml" if category == "mcp" else f"{category}.toml")


def test_shared_update_without_a_terminal_needs_yes(
    bare_project: Path, catalog: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review 2.9: no terminal means no answer, so it is refused, not read as "no"."""
    for name in ("nextjs", "react-native"):
        assert _template(bare_project, "vendored", name) == 0
    (catalog / "instructions/typescript.md").write_text("Updated shared advice\n")
    before = project_config_path(bare_project).read_bytes()

    assert _template(bare_project, "sync", "nextjs") == 2

    assert "--yes" in capsys.readouterr().err
    assert project_config_path(bare_project).read_bytes() == before
    assert _template(bare_project, "sync", "nextjs", "--yes") == 0
    assert (
        bare_project / "loadout/templates/instructions/typescript.md"
    ).read_text() == "Updated shared advice\n"


def test_a_catalog_under_a_linked_folder_above_it_still_loads(
    tmp_path: Path, catalog: Path
) -> None:
    """Review 3, batch 4.1: the check ran to `/`, so a catalog reached through a link
    above it, such as a home under a linked prefix, was refused."""
    alias = tmp_path / "alias"
    alias.symlink_to(catalog.parent.parent, target_is_directory=True)
    folder = alias / "loadout/templates"

    loaded = load_catalog(folder / "nextjs.toml")

    assert folder / "skills/review-typescript/SKILL.md" in loaded.files()


def test_a_link_inside_a_catalog_is_still_refused(tmp_path: Path, catalog: Path) -> None:
    (tmp_path / "elsewhere.md").write_text("x\n")
    (catalog / "skills/review-typescript/notes.md").symlink_to(tmp_path / "elsewhere.md")

    with pytest.raises(LoadoutError, match=r"path is a symlink: .*review-typescript/notes\.md"):
        load_catalog(catalog / "nextjs.toml")
