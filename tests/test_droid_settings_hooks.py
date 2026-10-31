"""Droid's `hooks` in settings.json become the hooks slice when there is no hooks.json.

Factory's hooks reference: "If `hooks.json` is absent, Droid also reads hook declarations from
the `hooks` key in the matching `settings.json`." Init left that key in the settings fragment,
so the first hook a user added made sync write hooks.json, and every hook in settings.json
stopped running without a word.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.migration import DROID_INERT_SETTINGS_HOOKS

pytestmark = pytest.mark.migration_integration

EVENTS = {"Stop": [{"hooks": [{"type": "command", "command": "/bin/echo done"}]}]}
FILE_EVENTS = {"PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/echo guard"}]}]}


def _project(root: Path, settings: dict[str, object]) -> Path:
    (root / ".factory").mkdir(parents=True)
    (root / ".factory/settings.json").write_text(json.dumps(settings))
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _init(root: Path, *flags: str) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "droid", *flags]


def test_global_init_moves_settings_hooks_into_the_hooks_slice(
    tmp_path: Path, fake_home: Path
) -> None:
    (fake_home / ".factory").mkdir()
    (fake_home / ".factory/settings.json").write_text(json.dumps({"model": "m", "hooks": EVENTS}))
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)

    argv = ["init", "global", str(source), "--harness", "droid", "--yes"]
    assert loadout.main(argv) == 0

    assert json.loads((source / "loadout/hooks/droid.json").read_text()) == EVENTS
    assert json.loads((source / "loadout/settings/droid.json").read_text()) == {"model": "m"}
    assert json.loads((fake_home / ".factory/hooks.json").read_text()) == EVENTS


def test_project_init_moves_settings_hooks_into_the_hooks_slice(tmp_path: Path) -> None:
    root = _project(tmp_path / "p", {"model": "m", "hooks": EVENTS})

    assert loadout.main(_init(root, "--yes")) == 0

    assert json.loads((root / "loadout/hooks.json").read_text()) == EVENTS
    assert json.loads((root / "loadout/settings/droid.json").read_text()) == {"model": "m"}
    assert json.loads((root / ".factory/hooks.json").read_text()) == EVENTS


def test_settings_hooks_beside_a_hooks_json_are_reported_and_stay_put(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p", {"model": "m", "hooks": EVENTS})
    (root / ".factory/hooks.json").write_text(json.dumps(FILE_EVENTS))

    assert loadout.main(_init(root, "--dry-run", "--json")) == 0
    notes = json.loads(capsys.readouterr().out)["notes"]
    assert loadout.main(_init(root, "--yes")) == 0

    settings = root / ".factory/settings.json"
    assert f"{settings}: {DROID_INERT_SETTINGS_HOOKS}" in notes
    assert json.loads((root / "loadout/hooks.json").read_text()) == FILE_EVENTS
    assert json.loads((root / "loadout/settings/droid.json").read_text()) == {
        "model": "m",
        "hooks": EVENTS,
    }
