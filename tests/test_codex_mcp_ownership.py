from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.written import owned_hashes, read_written, written_state_path
from registration import register

pytestmark = pytest.mark.integration

FOREIGN = '\n# Added by Codex\n[mcp_servers.runtime]\nurl = "https://runtime.example/mcp"\n'
SOURCE = '[managed]\ntransport = "http"\nurl = "https://managed.example/mcp"\n'


def setup(root: Path, home: Path) -> Path:
    register(root)
    (root / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[codex]\n')
    (root / "mcp.toml").write_text(SOURCE)
    target = home / ".codex/config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def run(root: Path, command: str = "sync", *args: str) -> int:
    return loadout.main([command, "--root", str(root), *args])


@pytest.mark.parametrize("force", [False, True])
def test_codex_added_servers_survive_sync_and_are_not_drift(
    tmp_path: Path, fake_home: Path, force: bool
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    before = target.read_bytes()

    assert run(tmp_path, "check") == 0
    assert run(tmp_path, "sync", *(["--force"] if force else [])) == 0
    assert target.read_bytes() == before
    assert set(tomllib.loads(target.read_text())["mcp_servers"]) == {"managed", "runtime"}
    assert read_written(tmp_path)[target].owned == ("mcp_servers.managed",)


def test_existing_foreign_server_survives_first_sync(tmp_path: Path, fake_home: Path) -> None:
    target = setup(tmp_path, fake_home)
    target.write_text(FOREIGN)

    assert run(tmp_path) == 0
    assert FOREIGN in target.read_text()
    assert set(tomllib.loads(target.read_text())["mcp_servers"]) == {"managed", "runtime"}


def test_source_updates_and_removals_preserve_foreign_servers(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    (tmp_path / "mcp.toml").write_text(SOURCE.replace("managed.example", "updated.example"))

    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text())["mcp_servers"]["managed"]["url"] == (
        "https://updated.example/mcp"
    )
    assert FOREIGN in target.read_text()
    (tmp_path / "mcp.toml").unlink()
    assert run(tmp_path, "check") == 1
    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text()) == {
        "mcp_servers": {"runtime": {"url": "https://runtime.example/mcp"}}
    }
    assert read_written(tmp_path)[target].owned == ()
    assert run(tmp_path, "check") == 0


def test_retired_server_names_can_be_reused_outside_loadout(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    (tmp_path / "mcp.toml").unlink()
    assert run(tmp_path) == 0
    target.write_text(
        target.read_text() + '\n[mcp_servers.managed]\nurl = "https://codex.example/mcp"\n'
    )
    before = target.read_bytes()

    assert run(tmp_path, "check") == 0
    assert run(tmp_path) == 0
    assert target.read_bytes() == before


@pytest.mark.parametrize("remove", [False, True])
def test_managed_server_edits_still_block_sync(
    tmp_path: Path, fake_home: Path, capsys, remove: bool
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    edited = "" if remove else target.read_text().replace("managed.example", "edited.example")
    target.write_text(edited + FOREIGN)
    before = target.read_bytes()
    capsys.readouterr()

    assert run(tmp_path, "check", "--json") == 1
    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(target), "kind": "owned-key-removed" if remove else "owned-key"}
    ]
    assert run(tmp_path) == 1
    assert target.read_bytes() == before
    assert run(tmp_path, "sync", "--force") == 0
    assert FOREIGN in target.read_text()
    assert tomllib.loads(target.read_text())["mcp_servers"]["managed"]["url"] == (
        "https://managed.example/mcp"
    )


def test_retiring_codex_preserves_foreign_servers(tmp_path: Path, fake_home: Path) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    (tmp_path / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[claude]\n')

    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text()) == {
        "mcp_servers": {"runtime": {"url": "https://runtime.example/mcp"}}
    }
    assert FOREIGN in target.read_text()


def test_disabling_mcp_while_settings_remain_retires_only_managed_servers(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "codex.json").write_text('{"model": "example-model"}')
    config = tmp_path / "loadout.toml"
    config.write_text(config.read_text() + 'defaults = "codex"\n')
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    config.write_text(config.read_text() + "mcp = false\n")

    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text()) == {
        "model": "example-model",
        "mcp_servers": {"runtime": {"url": "https://runtime.example/mcp"}},
    }
    assert FOREIGN in target.read_text()


def test_explicit_mcp_eviction_survives_retirement(tmp_path: Path, fake_home: Path, capsys) -> None:
    target = setup(tmp_path, fake_home)
    defaults = tmp_path / "defaults"
    defaults.mkdir()
    (defaults / "codex.json").write_text('{"model": "example-model", "$remove": ["mcp_servers"]}')
    config = tmp_path / "loadout.toml"
    config.write_text(config.read_text() + 'mcp = false\ndefaults = "codex"\n')
    target.write_text('sandbox_mode = "workspace-write"\n' + FOREIGN)
    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text()) == {
        "sandbox_mode": "workspace-write",
        "model": "example-model",
    }
    target.write_text(target.read_text() + FOREIGN)
    config.write_text('[[source]]\nname = "test"\npath = "."\n\n[claude]\n')
    capsys.readouterr()

    assert run(tmp_path, "check", "--json") == 1
    assert {"path": str(target), "kind": "orphaned"} in json.loads(capsys.readouterr().out)["drift"]
    assert run(tmp_path) == 0
    assert tomllib.loads(target.read_text()) == {"sandbox_mode": "workspace-write"}
    assert run(tmp_path, "check") == 0


def legacy_record(root: Path, target: Path) -> None:
    record = written_state_path(root)
    data = json.loads(record.read_text())
    data["written"][str(target)] = {
        "kind": "merged",
        "owned": ["mcp_servers"],
        "owned_sha256": owned_hashes(target.read_text(), ["mcp_servers"], "toml"),
    }
    record.write_text(json.dumps(data))


def test_upgrade_from_whole_table_ownership_preserves_foreign_servers(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    legacy_record(tmp_path, target)
    target.write_text(target.read_text() + FOREIGN)

    assert run(tmp_path, "check") == 0
    assert run(tmp_path) == 0
    assert FOREIGN in target.read_text()
    assert read_written(tmp_path)[target].owned == ("mcp_servers.managed",)


def test_legacy_record_does_not_make_an_edited_server_newly_managed(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    legacy_record(tmp_path, target)
    target.write_text(target.read_text().replace("managed.example", "edited.example") + FOREIGN)
    before = target.read_bytes()

    assert run(tmp_path) == 1
    assert target.read_bytes() == before


def test_retiring_a_legacy_record_preserves_servers_it_cannot_identify(
    tmp_path: Path, fake_home: Path
) -> None:
    target = setup(tmp_path, fake_home)
    assert run(tmp_path) == 0
    legacy_record(tmp_path, target)
    target.write_text(target.read_text() + FOREIGN)
    before = target.read_bytes()
    (tmp_path / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[claude]\n')

    assert run(tmp_path) == 0
    assert target.read_bytes() == before


def test_policy_only_servers_are_owned_and_retired(tmp_path: Path, fake_home: Path) -> None:
    target = setup(tmp_path, fake_home)
    permissions = tmp_path / "permissions.toml"
    permissions.write_text('[mcp]\ndeny = ["blocked/*", "blocked.tool/*"]\n')
    assert run(tmp_path) == 0
    target.write_text(target.read_text() + FOREIGN)
    assert tomllib.loads(target.read_text())["mcp_servers"]["blocked"] == {"enabled": False}
    assert tomllib.loads(target.read_text())["mcp_servers"]["blocked.tool"] == {"enabled": False}
    permissions.unlink()

    assert run(tmp_path) == 0
    assert set(tomllib.loads(target.read_text())["mcp_servers"]) == {"managed", "runtime"}
    assert run(tmp_path, "check") == 0
