"""Devin's global config.json: permissions, the hooks object and the settings fragment's
leaves, each owned by loadout, beside the keys the CLI writes for itself."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.files import check_all
from registration import register
from render_helpers import write_all

pytestmark = pytest.mark.integration

SETTINGS = {"permission_mode": "accept-edits", "agent": {"model": "swe"}}
HOOKS = {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "g.sh"}]}]}
EXISTING = {
    "version": 1,
    "devin": {"org_id": "org-1"},
    "agent": {"pinned_models": ["opus"]},
    "permissions": {"allow": ["Exec(old)"]},
    "theme_mode": "dark",
}


def build(root: Path, fake_home: Path, devin: str) -> Path:
    for folder in ("settings", "hooks"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "loadout.toml").write_text(
        f'[[source]]\nname = "test"\npath = "."\n\n[devin]\n{devin}', encoding="utf-8"
    )
    (root / "permissions.toml").write_text(
        '[shell]\nallow = ["ls"]\ndeny = ["rm"]\n', encoding="utf-8"
    )
    (root / "settings" / "devin.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    (root / "hooks" / "guard.json").write_text(json.dumps(HOOKS), encoding="utf-8")
    target = fake_home / ".config" / "devin" / "config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(EXISTING, indent=2) + "\n", encoding="utf-8")
    return target


def written(target: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
    return loaded


def test_permissions_hooks_and_settings_share_config_json_with_the_clis_keys(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    target = build(tmp_path, fake_home, 'settings = "devin"\nhooks = ["guard"]\n')

    write_all(tmp_path)

    assert written(target) == {
        "version": 1,
        "devin": {"org_id": "org-1"},
        "agent": {"pinned_models": ["opus"], "model": "swe"},
        "permissions": {"allow": ["Exec(ls)"], "ask": [], "deny": ["Exec(rm)"]},
        "theme_mode": "dark",
        "permission_mode": "accept-edits",
        "hooks": {"PreToolUse": [{"matcher": "^exec$", "hooks": HOOKS["PreToolUse"][0]["hooks"]}]},
    }
    assert check_all(render_all(tmp_path)) == []


def test_dropping_the_hooks_slice_removes_its_key_and_keeps_the_rest(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    target = build(tmp_path, fake_home, 'settings = "devin"\nhooks = ["guard"]\n')
    write_all(tmp_path)

    (tmp_path / "hooks" / "guard.json").write_text("{}", encoding="utf-8")
    write_all(tmp_path)

    document = written(target)
    assert "hooks" not in document
    assert document["permission_mode"] == "accept-edits"
    assert document["devin"] == {"org_id": "org-1"}


def test_a_settings_fragment_that_sets_a_hooks_key_is_refused(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    build(tmp_path, fake_home, 'settings = "devin"\nhooks = ["guard"]\n')
    (tmp_path / "settings" / "devin.json").write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    with pytest.raises(LoadoutError, match="the settings fragment sets hooks, which the devin"):
        write_all(tmp_path)
