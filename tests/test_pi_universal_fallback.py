"""Loadout leaves pi-permission-system's universal fallback, top-level `"*"`, to the extension.

With no top-level `"*"` the extension resolves every tool no surface names to `ask`. Loadout
wrote `"*": "allow"` through 0.9.3, which let every tool no surface names, such as `read` and
`write`, run without asking.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import loadout
from loadout.permissions import renderers
from loadout.permissions.renderers import render_pi
from loadout.permissions.rules import Rules

CONFIG = ".pi/agent/extensions/pi-permission-system/config.json"


def _older(rules: Rules, base: dict[str, Any]) -> dict[str, Any]:
    document = render_pi(rules, base)
    document["permission"] = {"*": "allow", **document["permission"]}
    return document


@pytest.mark.integration
def test_sync_over_an_older_loadouts_file_rewrites_it_without_the_fallback(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "loadout.toml").write_text('[[source]]\nname = "me"\npath = "."\n\n[pi]\n')
    (source / "permissions.toml").write_text('[shell]\nallow = ["pwd"]\n')
    machine = fake_home / ".config" / "loadout"
    machine.mkdir(parents=True)
    (machine / "config.toml").write_text(f'source = "{source}"\n')
    config = fake_home / CONFIG
    spec = renderers.RENDERERS["pi"]
    assert isinstance(spec, renderers.JsonSpec)
    with monkeypatch.context() as patched:
        patched.setitem(renderers.RENDERERS, "pi", replace(spec, fn=_older))
        assert loadout.main(["sync", "--global"]) == 0
    assert json.loads(config.read_text())["permission"]["*"] == "allow"
    capsys.readouterr()

    assert loadout.main(["check", "--global", "--json"]) == 1
    drift = json.loads(capsys.readouterr().out)
    assert loadout.main(["sync", "--global"]) == 0

    assert [(d["path"], d["kind"]) for d in drift["drift"]] == [(str(config), "source-changed")]
    assert f"wrote {config}" in capsys.readouterr().out
    assert list(json.loads(config.read_text())["permission"]) == ["bash", "mcp__*"]
