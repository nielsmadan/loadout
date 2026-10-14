"""A project's own keys in a co-owned settings file live in the project source and reach a
teammate's clone (P-48): init adopts them into loadout/settings/<harness>.json, sync renders
them back, and an edit made outside loadout is flagged."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.emit import render_project

pytestmark = pytest.mark.migration_integration

HARNESSES = ("claude", "droid", "opencode")
CLAUDE = {"permissions": {"deny": ["Bash(rm:*)"]}, "env": {"NODE_ENV": "test"}}
DROID = {"model": "custom:fast", "commandBlocklist": ["rm"]}
OPENCODE = {
    "$schema": "https://opencode.ai/config.json",
    "theme": "tokyonight",
    "permission": {"bash": {"rm *": "deny"}},
}


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _files() -> dict[str, str]:
    return {
        ".claude/settings.json": json.dumps(CLAUDE),
        ".factory/settings.json": json.dumps(DROID),
        "opencode.json": json.dumps(OPENCODE),
    }


def _init(root: Path) -> int:
    flags = [arg for harness in HARNESSES for arg in ("--harness", harness)]
    return loadout.main(["init", "project", "--root", str(root), *flags, "--yes"])


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def test_init_adopts_the_keys_no_slice_owns(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", _files())

    assert _init(root) == 0

    assert _read(root / "loadout/settings/claude.json") == {"env": {"NODE_ENV": "test"}}
    assert _read(root / "loadout/settings/droid.json") == {"model": "custom:fast"}
    assert _read(root / "loadout/settings/opencode.json") == {
        "$schema": "https://opencode.ai/config.json",
        "theme": "tokyonight",
    }
    assert _read(root / ".claude/settings.json")["env"] == {"NODE_ENV": "test"}


def test_a_fresh_clone_and_sync_gets_the_project_settings(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", _files())
    assert _init(root) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "loadout")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(root), str(clone)], check=True)
    assert not (clone / ".claude/settings.json").exists()

    assert loadout.main(["sync", "--root", str(clone)]) == 0

    claude = _read(clone / ".claude/settings.json")
    assert claude["env"] == {"NODE_ENV": "test"}
    assert claude["permissions"]["deny"] == ["Bash(rm:*)"]
    assert _read(clone / "opencode.json")["theme"] == "tokyonight"
    droid = _read(clone / ".factory/settings.json")
    assert droid["model"] == "custom:fast"
    assert droid["commandBlocklist"] == ["rm"]


def test_a_converted_opencode_jsonc_reaches_a_fresh_clone(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", {"opencode.jsonc": '{\n  // dark\n  "theme": "x",\n}\n'})
    argv = ["init", "project", "--root", str(root), "--harness", "opencode", "--yes"]
    assert loadout.main(argv) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "loadout")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(root), str(clone)], check=True)

    assert loadout.main(["sync", "--root", str(clone)]) == 0

    assert _read(clone / "opencode.json")["theme"] == "x"
    assert not (root / "opencode.jsonc").exists()


def test_an_edit_to_an_adopted_key_outside_loadout_is_flagged(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", _files())
    assert _init(root) == 0
    document = _read(root / "opencode.json")
    (root / "opencode.json").write_text(json.dumps({**document, "theme": "light"}))
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(root / "opencode.json"), "kind": "hand-edited"}
    ]


def test_the_settings_source_is_the_residual_rather_than_the_file_on_disk(
    tmp_path: Path,
) -> None:
    root = tmp_path / "p"
    (root / "loadout/settings").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/settings/claude.json").write_text('{"env": {"A": "1"}}\n')
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text('{"env": {"A": "stale"}, "model": "x"}\n')

    rendered = json.loads(str(render_project(root)[root / ".claude/settings.json"]))

    assert rendered["env"] == {"A": "1"}
    assert list(rendered) == ["env", "permissions"]


def test_an_untracked_settings_value_never_reaches_committed_source(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", {"opencode.json": json.dumps(OPENCODE)})
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text(json.dumps(CLAUDE))

    assert _init(root) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "loadout")

    private = root / "loadout/settings/claude.json"
    assert _read(private) == {"env": {"NODE_ENV": "test"}}
    assert _git(root, "check-ignore", str(private)).stdout.strip() == str(private)
    committed = _git(root, "grep", "-l", "NODE_ENV", "HEAD", "--", ".", check=False)
    assert (committed.returncode, committed.stdout) == (1, "")
    assert _read(root / ".claude/settings.json")["env"] == {"NODE_ENV": "test"}


def test_a_project_droid_settings_file_is_not_treated_as_runtime_state(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p", {".factory/settings.json": json.dumps(DROID)})
    argv = ["init", "project", "--root", str(root), "--harness", "droid", "--dry-run", "--json"]

    assert loadout.main(argv) == 0

    originals = json.loads(capsys.readouterr().out)["originals"]
    assert {o["action"] for o in originals if o["path"].endswith(".factory/settings.json")} == {
        "generated"
    }
