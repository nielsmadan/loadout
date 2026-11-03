"""Init adopts Codex's own top-level settings into `defaults`, the slice that renders them.

They used to land in a `settings` fragment no Codex renderer reads, so a second machine got no
model, approval policy, sandbox mode or profiles, and a `/model` edit went unreported.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.errors import LoadoutError
from loadout.manifest import load_manifest

pytestmark = pytest.mark.migration_integration

CONFIG = """\
model = "gpt-5.5"
approval_policy = "on-request"
sandbox_mode = "workspace-write"

[profiles.fast]
model = "gpt-5.5-mini"

[projects."/work/app"]
trust_level = "trusted"

[mcp_servers.docs]
command = "docs-server"
"""
ADOPTED = {
    "model": "gpt-5.5",
    "approval_policy": "on-request",
    "sandbox_mode": "workspace-write",
    "profiles": {"fast": {"model": "gpt-5.5-mini"}},
}


@pytest.fixture
def adopted(tmp_path: Path, fake_home: Path) -> tuple[Path, Path]:
    config = fake_home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(CONFIG)
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0
    return source / "loadout", config


def test_init_adopts_codex_settings_as_defaults(adopted: tuple[Path, Path]) -> None:
    source, _ = adopted
    block = tomllib.loads((source / "loadout.toml").read_text())["codex"]
    assert block["defaults"] == ["codex"]
    assert json.loads((source / "defaults/codex.json").read_text()) == ADOPTED
    assert sorted(p.name for p in (source / "defaults").iterdir()) == [
        "codex.json",
        "codex.owned",
    ]


def test_a_machine_without_codex_config_gets_the_adopted_settings(
    adopted: tuple[Path, Path], capsys
) -> None:
    _, config = adopted
    config.unlink()
    assert loadout.main(["sync", "--global"]) == 0
    rendered = tomllib.loads(config.read_text())
    assert {key: rendered[key] for key in ADOPTED} == ADOPTED
    assert rendered["mcp_servers"] == {"docs": {"command": "docs-server"}}


def test_a_model_change_in_codex_is_drift(adopted: tuple[Path, Path], capsys) -> None:
    _, config = adopted
    capsys.readouterr()
    config.write_text(config.read_text().replace('"gpt-5.5"', '"gpt-5.5-codex"', 1))
    assert loadout.main(["check", "--global", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert [(d["path"], d["kind"]) for d in report["drift"]] == [(str(config), "owned-key")]


def test_a_codex_settings_fragment_is_refused_toward_defaults(tmp_path: Path) -> None:
    (tmp_path / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[codex]\nsettings = ["codex"]\n'
    )
    with pytest.raises(LoadoutError) as error:
        load_manifest(tmp_path / "loadout.toml")
    assert str(error.value) == CODEX_SETTINGS_REFUSAL


CODEX_SETTINGS_REFUSAL = (
    "codex: `settings` renders nothing for Codex, whose config.toml keys come from "
    "`defaults`; move the fragment to defaults/<name>.json and name it under `defaults`"
)
