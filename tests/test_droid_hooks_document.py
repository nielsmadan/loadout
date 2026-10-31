"""Droid's standalone hooks.json is keyed by event name at the top level.

Factory's hooks reference: "Standalone `hooks.json` files are keyed directly by event
name"; only hooks declared inside `settings.json` sit under a `hooks` key. droid exec
0.251.0 did not run a hook under a `hooks` key in hooks.json and did run the same hook
at the top level, so the wrapped document loadout wrote was never run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration

EVENTS = {
    "PreToolUse": [
        {"matcher": "Execute", "hooks": [{"type": "command", "command": "/bin/echo guard"}]}
    ],
    "Stop": [{"hooks": [{"type": "command", "command": "/bin/echo done"}]}],
}


def test_sync_writes_the_global_hooks_json_keyed_by_event(tmp_path: Path, fake_home: Path) -> None:
    source = tmp_path / "source"
    (source / "hooks").mkdir(parents=True)
    (source / "hooks/droid.json").write_text(json.dumps(EVENTS))
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[droid]\nhooks = ["droid"]\n'
    )
    register(source)

    assert loadout.main(["sync", "--global"]) == 0

    assert json.loads((fake_home / ".factory/hooks.json").read_text()) == EVENTS


def test_sync_writes_the_project_hooks_json_keyed_by_event(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["droid"]\n')
    (root / "loadout/hooks.json").write_text(json.dumps(EVENTS))

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads((root / ".factory/hooks.json").read_text()) == EVENTS


def _init_project(root: Path, document: dict[str, object], capsys) -> list[str]:
    """Applies init and returns the notes its preview gave."""
    (root / ".factory").mkdir(parents=True)
    (root / ".factory/hooks.json").write_text(json.dumps(document))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    argv = ["init", "project", "--root", str(root), "--harness", "droid"]
    assert loadout.main([*argv, "--dry-run", "--json"]) == 0
    notes: list[str] = json.loads(capsys.readouterr().out)["notes"]
    assert loadout.main([*argv, "--yes"]) == 0
    return notes


def test_init_adopts_a_hooks_json_keyed_by_event(tmp_path: Path, capsys) -> None:
    root = tmp_path / "p"
    _init_project(root, EVENTS, capsys)

    assert json.loads((root / "loadout/hooks.json").read_text()) == EVENTS
    assert json.loads((root / ".factory/hooks.json").read_text()) == EVENTS


def test_init_reports_hooks_wrapped_in_a_hooks_key_and_does_not_adopt_them(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "p"
    hooks = root / ".factory/hooks.json"

    notes = _init_project(root, {"hooks": EVENTS, "Stop": EVENTS["Stop"]}, capsys)

    assert f"{hooks}: {WRAPPED}" in notes
    assert json.loads((root / "loadout/hooks.json").read_text()) == {"Stop": EVENTS["Stop"]}


WRAPPED = (
    "hooks.json wraps events in a `hooks` key, which Droid reads only in settings.json, so "
    "those hooks never ran and are not adopted; to keep them, move each event to the top "
    "level of the hooks fragment"
)
