"""Cursor's settings: keys of a JSON fragment that loadout owns, leaf by leaf, inside
the cli-config.json the CLI shares with its own state."""

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

FRAGMENT = {
    "approvalMode": "unrestricted",
    "sandbox": {"mode": "disabled"},
    "selectedModel": {
        "modelId": "claude-sonnet-5-5",
        "parameters": [{"id": "reasoning_effort", "value": "medium"}],
    },
    "hasChangedDefaultModel": True,
}

EXISTING = {
    "version": 1,
    "permissions": {"allow": ["Shell(old)"], "deny": []},
    "approvalMode": "allowlist",
    "sandbox": {"mode": "enabled", "networkAccess": "allowlist"},
    "authInfo": {"email": "me@example.com"},
}


def build(root: Path, fragment: dict[str, object], cursor: str = "") -> Path:
    (root / "settings").mkdir(parents=True, exist_ok=True)
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[cursor]\nsettings = "cursor"\n' + cursor,
        encoding="utf-8",
    )
    (root / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    (root / "settings" / "cursor.json").write_text(json.dumps(fragment), encoding="utf-8")
    return root


def prepare(root: Path, fake_home: Path, fragment: dict[str, object]) -> Path:
    build(root, fragment)
    target = fake_home / ".cursor" / "cli-config.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(EXISTING, indent=2) + "\n", encoding="utf-8")
    return target


def written(target: Path) -> dict[str, object]:
    loaded: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
    return loaded


def test_the_fragments_keys_land_beside_permissions_and_the_clis_keys_stay(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    target = prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    assert written(target) == {
        "version": 1,
        "permissions": {"allow": ["Shell(ls)"], "deny": []},
        "approvalMode": "unrestricted",
        "sandbox": {"mode": "disabled", "networkAccess": "allowlist"},
        "authInfo": {"email": "me@example.com"},
        "selectedModel": FRAGMENT["selectedModel"],
        "hasChangedDefaultModel": True,
    }


def test_a_key_dropped_from_the_fragment_is_removed_and_the_rest_stay(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    target = prepare(tmp_path, fake_home, FRAGMENT)
    write_all(tmp_path)

    kept = {"approvalMode": "unrestricted"}
    (tmp_path / "settings" / "cursor.json").write_text(json.dumps(kept), encoding="utf-8")
    write_all(tmp_path)
    result = written(target)

    assert "selectedModel" not in result
    assert "hasChangedDefaultModel" not in result
    assert result["sandbox"] == {"networkAccess": "allowlist"}
    assert result["approvalMode"] == "unrestricted"
    assert result["authInfo"] == {"email": "me@example.com"}


def test_the_record_lists_the_leaves_beside_the_fragment(tmp_path: Path, fake_home: Path) -> None:
    register(tmp_path)
    prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    record = tmp_path / "settings" / "cursor.owned"
    keys = [line for line in record.read_text(encoding="utf-8").splitlines() if line[:1] != "#"]
    assert keys == [
        "approvalMode",
        "hasChangedDefaultModel",
        "sandbox.mode",
        "selectedModel.modelId",
        "selectedModel.parameters",
    ]


def test_the_cli_rewriting_the_model_is_drift(tmp_path: Path, fake_home: Path) -> None:
    register(tmp_path)
    target = prepare(tmp_path, fake_home, FRAGMENT)
    write_all(tmp_path)
    assert check_all(render_all(tmp_path)) == []

    rewritten = written(target)
    rewritten["selectedModel"] = {"modelId": "composer-2-5", "parameters": []}
    target.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")

    assert [item.path for item in check_all(render_all(tmp_path))] == [target]


def test_the_cli_changing_a_key_loadout_does_not_own_is_not_drift(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    target = prepare(tmp_path, fake_home, FRAGMENT)
    write_all(tmp_path)

    rewritten = written(target)
    rewritten["sandbox"] = {"mode": "disabled", "networkAccess": "open"}
    target.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")

    assert check_all(render_all(tmp_path)) == []


def test_a_fragment_setting_permissions_is_refused(tmp_path: Path, fake_home: Path) -> None:
    register(tmp_path)
    build(tmp_path, {"permissions": {"allow": ["Shell(rm)"]}})

    with pytest.raises(LoadoutError, match="permissions"):
        render_all(tmp_path)


def test_settings_without_the_permissions_slice_is_refused(tmp_path: Path, fake_home: Path) -> None:
    register(tmp_path)
    build(tmp_path, FRAGMENT, "permissions = false\n")

    with pytest.raises(LoadoutError, match="renders only through the permissions slice"):
        render_all(tmp_path)


def test_settings_render_with_permissions_selecting_no_rules(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    build(tmp_path, FRAGMENT, "permissions = []\n")
    target = fake_home / ".cursor" / "cli-config.json"

    write_all(tmp_path)

    assert written(target)["approvalMode"] == "unrestricted"
    assert written(target)["permissions"] == {"allow": [], "deny": []}


def test_remove_is_refused_since_leaving_a_key_out_stops_managing_it(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    build(tmp_path, {"approvalMode": "unrestricted", "$remove": ["sandbox.mode"]})

    with pytest.raises(LoadoutError, match=r"\$remove"):
        render_all(tmp_path)
