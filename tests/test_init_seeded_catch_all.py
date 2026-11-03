"""Init says so when it seeds a shell catch-all that no original stated.

A default OpenCode, whose opencode.jsonc holds only `$schema`, asked before no shell command;
after init it asked before every one, and the only trace was the preview diff.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

OPENCODE = (
    "OpenCode will now ask before any shell command no rule allows or denies; set [shell] "
    "default in the source to change that."
)
PI = (
    "Pi's permission extension will now ask before any shell command no rule allows or denies; "
    "set [shell] default in the source to change that."
)
OPENCODE_CONFIG = ".config/opencode/opencode.jsonc"
PI_CONFIG = ".pi/agent/extensions/pi-permission-system/config.json"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "agent-config"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _notes(source: Path, capsys, *harnesses: str) -> list[str]:
    argv = ["init", "global", str(source), "--dry-run", "--json"]
    argv += [flag for harness in harnesses for flag in ("--harness", harness)]
    assert loadout.main(argv) == 0, capsys.readouterr()
    return json.loads(capsys.readouterr().out)["notes"]


def _write(home: Path, relative: str, document: object) -> None:
    path = home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


@pytest.mark.parametrize(
    "document", [{"$schema": "https://opencode.ai/config.json"}, None], ids=["schema", "absent"]
)
def test_an_unstated_opencode_catch_all_is_noted(
    source: Path, fake_home: Path, capsys, document: object
) -> None:
    (fake_home / ".config/opencode").mkdir(parents=True)
    if document is not None:
        _write(fake_home, OPENCODE_CONFIG, document)

    assert OPENCODE in _notes(source, capsys, "opencode")


@pytest.mark.parametrize("verdict", ["ask", "allow"])
def test_a_stated_opencode_catch_all_is_not_noted(
    source: Path, fake_home: Path, capsys, verdict: str
) -> None:
    _write(fake_home, OPENCODE_CONFIG, {"permission": {"bash": {"*": verdict, "pwd": "allow"}}})

    assert OPENCODE not in _notes(source, capsys, "opencode")


def test_an_unstated_pi_catch_all_is_noted(source: Path, fake_home: Path, capsys) -> None:
    _write(fake_home, PI_CONFIG, {"permission": {"bash": {"pwd": "allow"}}})

    assert PI in _notes(source, capsys, "pi")


def test_a_catch_all_one_harness_states_renders_for_both(
    source: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home, OPENCODE_CONFIG, {"$schema": "https://opencode.ai/config.json"})
    _write(fake_home, PI_CONFIG, {"permission": {"bash": {"*": "allow"}}})

    notes = _notes(source, capsys, "opencode", "pi")
    assert OPENCODE not in notes
    assert PI not in notes


def test_an_unstated_opencode_catch_all_is_noted_in_a_project(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "opencode.json").write_text('{"$schema": "https://opencode.ai/config.json"}\n')
    subprocess.run(["git", "init", "-q", str(root)], check=True)

    argv = ["init", "project", "--root", str(root), "--harness", "opencode", "--dry-run", "--json"]
    assert loadout.main(argv) == 0, capsys.readouterr()
    assert OPENCODE in json.loads(capsys.readouterr().out)["notes"]
