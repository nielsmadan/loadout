"""`settings` under [pi] without `plugins` is a manifest error, not a note.

Pi's settings.json is written only through its plugins document, so removing `plugins` left
the fragment inert and let the next sync retire the whole file, `defaultModel` included, with
only a note to say so.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration

ERROR = (
    "loadout: pi: `settings` renders only through the plugins slice; declare `plugins` under "
    "[pi], even as an empty list\n"
)


def _source(root: Path, manifest: str) -> Path:
    (root / "settings").mkdir(parents=True)
    (root / "settings/pi.json").write_text('{"defaultModel": "m"}\n')
    (root / "loadout.toml").write_text(f'[[source]]\nname = "s"\npath = "."\n\n{manifest}')
    return register(root)


def test_dropping_plugins_stops_sync_before_it_retires_settings(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _source(tmp_path / "source", '[pi]\nsettings = ["pi"]\nplugins = []\n')
    assert loadout.main(["sync", "--global"]) == 0
    settings = fake_home / ".pi/agent/settings.json"
    rendered = settings.read_text()
    capsys.readouterr()
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[pi]\nsettings = ["pi"]\n'
    )

    for command in ("sync", "check"):
        assert loadout.main([command, "--global"]) == 3
        assert capsys.readouterr().err == ERROR
    assert settings.read_text() == rendered


def test_declaring_plugins_renders_the_settings(tmp_path: Path, fake_home: Path, capsys) -> None:
    _source(tmp_path / "source", '[pi]\nsettings = ["pi"]\nplugins = []\n')
    assert loadout.main(["sync", "--global"]) == 0

    assert json.loads((fake_home / ".pi/agent/settings.json").read_text())["defaultModel"] == "m"


def test_shared_settings_in_all_leave_pi_without_plugins_alone(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    (fake_home / ".claude").mkdir()
    _source(tmp_path / "source", '[all]\nsettings = ["pi"]\n\n[claude]\n[pi]\n')
    assert loadout.main(["sync", "--global"]) == 0

    assert json.loads((fake_home / ".claude/settings.json").read_text())["defaultModel"] == "m"
