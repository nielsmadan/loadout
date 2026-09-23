from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from loadout import migration_git, migration_transaction
from loadout.bundled_templates import BUNDLED, STARTERS, bundled_template
from loadout.cli import main
from loadout.discovery import discover
from loadout.machine import machine_config_path
from loadout.migration import plan_migration
from loadout.migration_journal import Journal
from loadout.migration_models import MigrationPlan
from loadout.migration_transaction import (
    MigrationFailure,
    MigrationPreparation,
    apply_migration,
)
from loadout.output import CATEGORIES
from loadout.project import project_config_path
from loadout.templates import resolve_template, vendored_path

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Starter fixture")
    _git(root, "config", "user.email", "starter@example.invalid")


def _source(fake_home: Path, *names: str) -> tuple[Path, ...]:
    global_root = fake_home / "source"
    global_root.mkdir()
    paths = []
    manifest = []
    for name in names:
        tree = global_root / name / "templates/frontend"
        tree.mkdir(parents=True)
        (tree / "instructions.md").write_text(f"{name} advice\n")
        paths.append(tree)
        manifest.append(f'[[source]]\nname = "{name}"\npath = "{name}"\nuse = ["templates"]\n')
    (global_root / "loadout.toml").write_text(
        'artifacts = "artifacts.toml"\n' + "\n".join(manifest)
    )
    (global_root / "artifacts.toml").write_text("artifact = []\n")
    config = machine_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(f'source = "{global_root}"\n')
    return tuple(paths)


def _materialize(plan: MigrationPlan, root: Path) -> None:
    for write in plan.source_writes:
        path = root / "loadout" / write.path.relative_to(plan.inventory.source_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(write.content)
        path.chmod(write.mode)


def _init(root: Path, starter: str | None = None) -> list[str]:
    args = ["init", "project", "--harness", "claude", "--root", str(root)]
    return args + (["--starter", starter] if starter else [])


@pytest.mark.parametrize("name", STARTERS)
def test_bundled_templates_resolve_offline_without_machine_config(
    tmp_path: Path, name: str
) -> None:
    found = resolve_template(name, tmp_path)
    assert found.source == BUNDLED
    assert found.path == bundled_template(name)
    assert {p.name for p in found.path.iterdir() if p.is_dir()} == CATEGORIES
    assert len((found.path / "instructions.md").read_text().split()) > 35
    assert (found.path / "permissions.toml").read_bytes().strip() == b""
    assert (found.path / "mcp.toml").read_bytes().strip() == b""


def test_installed_wheel_renders_bundled_templates_offline(tmp_path: Path) -> None:
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert built.returncode == 0, built.stderr.decode()
    (wheel,) = tmp_path.glob("*.whl")
    installed = tmp_path / "installed"
    with ZipFile(wheel) as archive:
        archive.extractall(installed)
    script = """
import pathlib, socket, sys
sys.path.insert(0, sys.argv[1])
import loadout
from loadout.output import CATEGORIES
from loadout.templates import resolve_template, copy_tree, declare, record_hash, tree_hash
from loadout.project import load_project_config, project_config_path
from loadout.emit import render_project
assert pathlib.Path(loadout.__file__).is_relative_to(sys.argv[1])
socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network access'))
for name in ('frontend', 'backend'):
    root = pathlib.Path(sys.argv[2]) / name
    (root / 'loadout').mkdir(parents=True)
    project_config_path(root).write_text('harnesses = ["claude"]\\n')
    (root / 'loadout/permissions.toml').write_text('')
    found = resolve_template(name, root)
    assert found.source == '(bundled)'
    assert all((found.path / category / '.gitkeep').is_file() for category in CATEGORIES)
    local = root / 'loadout/templates' / name
    copy_tree(found.path, local)
    declare(root, name)
    record_hash(root, name, tree_hash(local))
    config = load_project_config(project_config_path(root))
    assert config.vendored_hash(name) == tree_hash(found.path)
    assert '# ' + name.capitalize() + ' work' in render_project(root)[root / 'CLAUDE.md']
print('both installed starters rendered offline')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed), str(tmp_path / "projects")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "both installed starters rendered offline"


@pytest.mark.parametrize("mode", ("tracked", "vendored"))
def test_native_template_commands_refuse_missing_route_before_mutation(
    tmp_path: Path, mode: str
) -> None:
    source = tmp_path / "loadout"
    source.mkdir()
    config = source / "config.toml"
    config.write_text('harnesses = ["claude"]\npresets = false\nartifacts = "artifacts.toml"\n')
    (source / "artifacts.toml").write_text("artifact = []\n")
    before = config.read_bytes()
    assert main(["template", "add", "frontend", "--" + mode, "--root", str(tmp_path)]) == 3
    assert config.read_bytes() == before
    assert not (source / "templates").exists()


@pytest.mark.parametrize("mode", ("tracked", "vendored"))
def test_populated_native_template_command_preflights_before_mutation(
    tmp_path: Path, fake_home: Path, mode: str
) -> None:
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert plan.complete, plan.issues
    _materialize(plan, tmp_path)
    (upstream,) = _source(fake_home, "company")
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["npm"]\n')
    before = project_config_path(tmp_path).read_bytes()
    assert main(["template", "add", "frontend", "--" + mode, "--root", str(tmp_path)]) == 3
    assert project_config_path(tmp_path).read_bytes() == before
    assert not vendored_path(tmp_path, "frontend").exists()


def _interrupted_starter(
    prepared: MigrationPreparation, phase: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    def fail(*args: object) -> None:
        raise KeyboardInterrupt()

    real_replace = migration_git.replace_index
    count = 0

    def replace_index(path: Path, before: bytes | None, after: bytes | None) -> None:
        nonlocal count
        count += 1
        real_replace(path, before, after)
        if count == 2:
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        if phase == "checkpoint":
            patch.setattr(migration_git, "checkpoint", fail)
        elif phase == "finish":
            patch.setattr(migration_transaction, "_finish", fail)
        else:
            assert phase == "after-index"
            patch.setattr(migration_git, "replace_index", replace_index)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    assert (
        Journal.load(failure.value.journal).status
        == {
            "checkpoint": "checkpoint",
            "finish": "apply",
            "after-index": "stage-index",
        }[phase]
    )
    return failure.value.journal


def _upstream_privacy_rules(
    upstream_repo: Path, fake_home: Path, policy: str
) -> tuple[Path, Path | None]:
    if policy == "gitignore":
        rules = upstream_repo / ".gitignore"
        rules.write_text("")
        return rules, None
    rules = fake_home / "upstream-ignore-rules"
    rules.write_text("")
    if policy == "core-excludes":
        link = fake_home / "upstream-excludes"
        _git(upstream_repo, "config", "core.excludesFile", str(link))
    else:
        link = upstream_repo / ".git/info/exclude"
        link.unlink()
    link.symlink_to(rules)
    return rules, link


@pytest.mark.parametrize("starter", STARTERS)
def test_unresolved_project_starter_does_not_mutate(
    tmp_path: Path, starter: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(["init", "project", "--root", str(tmp_path), "--starter", starter, "--yes", "--json"])
        == 2
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["issues"] and not preview["complete"]
    assert list(tmp_path.iterdir()) == []
