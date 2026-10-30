"""A `settings` fragment no declared slice reads is named, with the slice that would.

Pi's settings.json is written only through its plugins document, so `[pi] settings` without
`plugins` rendered nothing and said nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration

NOTE = (
    "pi.settings: `settings` under [pi] renders nothing, because no slice that reads it is "
    "declared; declare `plugins` under [pi], even as an empty list"
)


def _source(root: Path, block: str) -> Path:
    (root / "settings").mkdir(parents=True)
    (root / "settings/pi.json").write_text('{"theme": "dark"}\n')
    (root / "loadout.toml").write_text(f'[[source]]\nname = "s"\npath = "."\n\n[pi]\n{block}')
    return register(root)


def _notes(capsys) -> list[str]:
    assert loadout.main(["check", "--global", "--json"]) == 0
    return [n for n in json.loads(capsys.readouterr().out)["notes"] if n.startswith("pi.settings")]


def test_pi_settings_without_plugins_is_noted(tmp_path: Path, fake_home: Path, capsys) -> None:
    _source(tmp_path / "source", 'settings = ["pi"]\n')
    assert loadout.main(["sync", "--global"]) == 0
    assert f"note: {NOTE}" in capsys.readouterr().out.splitlines()

    assert _notes(capsys) == [NOTE]


def test_declaring_plugins_renders_the_settings(tmp_path: Path, fake_home: Path, capsys) -> None:
    _source(tmp_path / "source", 'settings = ["pi"]\nplugins = []\n')
    assert loadout.main(["sync", "--global"]) == 0
    capsys.readouterr()

    assert _notes(capsys) == []
    assert json.loads((fake_home / ".pi/agent/settings.json").read_text())["theme"] == "dark"
