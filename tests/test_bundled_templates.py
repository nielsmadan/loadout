from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from loadout.bundled_templates import BUNDLED, STARTERS, bundled_template
from loadout.output import CATEGORIES
from loadout.templates import resolve_template

pytestmark = pytest.mark.integration

ROOT = Path(__file__).parents[1]


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
