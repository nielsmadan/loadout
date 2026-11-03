"""`init project --root` initialises the folder it names, also below the repository's top level.

Sync, check and the Git hooks already serve a project root inside a monorepo. Init resolved the
named folder up to the Git top level instead, adopted nothing of the subfolder and reported an
exact reproduction of a setup it never read.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

SOURCE_EXISTS = (
    "init would write this source file, but it already exists; move it aside or delete it, "
    "then run init again"
)


def _monorepo(tmp_path: Path) -> Path:
    mono = tmp_path / "mono"
    web = mono / "apps/web"
    (web / ".claude").mkdir(parents=True)
    (mono / "README.md").write_text("mono\n")
    (web / "CLAUDE.md").write_text("# web\n")
    (web / ".claude/settings.json").write_text('{"permissions": {"allow": ["Bash(make)"]}}\n')
    subprocess.run(["git", "init", "-q", str(mono)], check=True)
    return web


def test_init_project_initialises_the_subfolder_it_names(tmp_path: Path, capsys) -> None:
    web = _monorepo(tmp_path)
    argv = ["init", "project", "--root", str(web), "--harness", "claude"]

    assert loadout.main([*argv, "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["root"], preview["source_root"]) == (str(web), str(web / "loadout"))

    assert loadout.main([*argv, "--yes"]) == 0
    rules = tomllib.loads((web / "loadout/permissions.toml").read_text())
    assert rules["shell"]["allow"] == ["=make"]
    assert (web / "loadout/instructions/main.md").read_text() == "# web\n"
    assert loadout.main(["sync", "--root", str(web)]) == 0
    assert loadout.main(["check", "--root", str(web)]) == 0
    assert sorted(p.name for p in (tmp_path / "mono").iterdir()) == [".git", "README.md", "apps"]


def test_init_project_without_root_resolves_the_enclosing_repository(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    web = _monorepo(tmp_path)
    monkeypatch.chdir(web)
    assert loadout.main(["init", "project", "--harness", "claude", "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["source_root"] == str(tmp_path / "mono/loadout")


def test_an_existing_source_file_names_a_real_next_step(tmp_path: Path, capsys) -> None:
    web = _monorepo(tmp_path)
    (web / "loadout").mkdir()
    (web / "loadout/permissions.toml").write_text("[shell]\n")
    argv = ["init", "project", "--root", str(web), "--harness", "claude", "--dry-run", "--json"]
    assert loadout.main(argv) == 2
    preview = json.loads(capsys.readouterr().out)
    assert [(i["code"], i["paths"], i["message"]) for i in preview["issues"]] == [
        ("source-exists", [str(web / "loadout/permissions.toml")], SOURCE_EXISTS)
    ]


def test_a_subfolder_of_an_initialised_project_gets_its_own_source(tmp_path: Path, capsys) -> None:
    web = _monorepo(tmp_path)
    mono = tmp_path / "mono"
    assert (
        loadout.main(["init", "project", "--root", str(mono), "--harness", "claude", "--yes"]) == 0
    )
    capsys.readouterr()
    argv = ["init", "project", "--root", str(web), "--harness", "claude", "--dry-run", "--json"]
    assert loadout.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    assert (preview["source_root"], preview["initialized"]) == (str(web / "loadout"), None)
