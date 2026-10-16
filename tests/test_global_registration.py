"""Global scope renders only from this machine's registered source; a `loadout.toml`
anywhere else is refused before any of it is read or written (ADR 0031)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.written import written_state_path
from registration import register

pytestmark = pytest.mark.integration

REFUSAL = "isn't this machine's registered source; register it with `loadout init global <path>`"


def _clone(root: Path, destination: str) -> Path:
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("echo pwned\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        f'[instructions.evil]\ndestinations = ["{destination}"]\norder = ["x"]\n'
    )
    return root


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.parametrize("machine", ["none", "elsewhere"])
def test_a_cloned_global_setup_is_refused_before_it_writes(
    tmp_path: Path, fake_home: Path, capsys, machine: str
) -> None:
    if machine == "elsewhere":
        register(tmp_path / "mine")
    clone = _clone(tmp_path / "clone", "~/.config/fish/conf.d/evil.fish")
    # A record of a vanished root, which a sync that got as far as pruning would delete.
    stale = written_state_path(tmp_path / "vanished")
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"root": str(tmp_path / "vanished")}))
    before = _tree(fake_home)

    assert loadout.main(["sync", "--root", str(clone)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {clone}: this folder holds a global setup (loadout.toml) that {REFUSAL}\n"
    )
    assert _tree(fake_home) == before


def test_check_of_a_cloned_global_setup_prints_only_the_refusal(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    key = fake_home / ".ssh/id_fake"
    key.parent.mkdir()
    key.write_text("FAKE PRIVATE KEY\n")
    clone = _clone(tmp_path / "clone", "~/.ssh/id_fake")

    assert loadout.main(["check", "--root", str(clone)]) == 3

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == (
        "",
        f"loadout: {clone}: this folder holds a global setup (loadout.toml) that {REFUSAL}\n",
    )


def test_check_json_reports_the_refusal_as_its_one_object(tmp_path: Path, capsys) -> None:
    clone = _clone(tmp_path / "clone", "~/out.md")

    assert loadout.main(["check", "--root", str(clone), "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["exit_code"]) == ("error", 3)
    assert result["error"].endswith(REFUSAL)


def test_the_refusal_comes_before_the_manifest_is_parsed(tmp_path: Path, capsys) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "loadout.toml").write_text("this is [not toml\n")

    assert loadout.main(["sync", "--root", str(clone)]) == 3

    assert capsys.readouterr().err.endswith(f"{REFUSAL}\n")


def _project_with_a_pr_manifest(root: Path) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    return _clone(root, "~/.config/fish/conf.d/evil.fish")


def test_a_project_holding_a_pr_added_global_setup_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    project = _project_with_a_pr_manifest(tmp_path / "p")
    home = _tree(fake_home)
    tree = _tree(project)

    assert loadout.main(["sync", "--root", str(project)]) == 3

    assert capsys.readouterr().err.endswith(f"{REFUSAL}\n")
    assert (_tree(fake_home), _tree(project)) == (home, tree)


def test_a_staged_global_setup_in_a_project_is_refused(tmp_path: Path, capsys) -> None:
    project = _project_with_a_pr_manifest(tmp_path / "p")
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    for command in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(project), *command], check=True, env=environment)
    (project / "loadout.toml").unlink()

    assert loadout.main(["check", "--staged", "--root", str(project)]) == 3

    assert capsys.readouterr().err.endswith(f"{REFUSAL}\n")


def test_init_project_refuses_a_folder_holding_a_global_setup(tmp_path: Path, capsys) -> None:
    project = _clone(tmp_path / "p", "~/.config/fish/conf.d/evil.fish")
    tree = _tree(project)

    assert loadout.main(["init", "project", "--root", str(project), "--yes"]) == 3

    assert capsys.readouterr().err.endswith(f"{REFUSAL}\n")
    assert _tree(project) == tree


def _source(root: Path) -> Path:
    return _clone(root, "~/global.md")


@pytest.mark.parametrize("spelling", ["global", "root", "cwd", "link", "relative"])
def test_the_registered_source_syncs_however_it_is_named(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    source = register(_source(tmp_path / "source"))
    (tmp_path / "alias").symlink_to(source, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    arguments = {
        "global": ["--global"],
        "root": ["--root", str(source)],
        "cwd": [],
        "link": ["--root", str(tmp_path / "alias")],
        "relative": ["--root", "source"],
    }[spelling]
    if spelling == "cwd":
        monkeypatch.chdir(source)

    assert loadout.main(["sync", *arguments]) == 0

    assert (fake_home / "global.md").read_text().endswith("echo pwned\n")
