"""The guard, the registration gate and the staged narrowing, called as functions so
`just check` holds them too: until now only the integration tier did (review 3, batch 5.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout import staged, sync
from loadout.commands import cmd_sync
from loadout.errors import LoadoutError
from loadout.machine import registered_source
from loadout.output import Merged
from loadout.staged import Entry, _render_inputs
from loadout.written import merged_entry, record_written
from registration import register

SERVERS = {"docs": {"command": "docs-mcp"}}


def _shared(fake_home: Path, servers: dict[str, object]) -> Path:
    path = fake_home / ".claude.json"
    path.write_text(json.dumps({"numStartups": 3, "mcpServers": servers}, indent=2) + "\n")
    return path


def _recorded(tmp_path: Path, fake_home: Path) -> tuple[sync.SyncState, dict[Path, Merged]]:
    root = tmp_path / "source"
    root.mkdir()
    path = _shared(fake_home, SERVERS)
    document = json.dumps({"mcpServers": SERVERS})
    outputs = {path: Merged(frozenset({"mcpServers"}), document, format="json")}
    record_written(root, {path: merged_entry({"mcpServers"}, document, "json")})
    return sync.sync_state(root), outputs


def test_an_owned_key_as_recorded_is_accepted(tmp_path: Path, fake_home: Path) -> None:
    state, outputs = _recorded(tmp_path, fake_home)

    assert sync.unaccepted_outputs(state, outputs) == []


def test_an_owned_key_edited_since_the_record_is_refused(tmp_path: Path, fake_home: Path) -> None:
    state, outputs = _recorded(tmp_path, fake_home)
    path = _shared(fake_home, {**SERVERS, "mine": {"command": "mine-mcp"}})

    refused = sync.unaccepted_outputs(state, outputs)

    assert [(item.path, item.kind) for item in refused] == [(path, "owned-key")]


def test_a_harness_key_beside_the_owned_one_is_the_harness_s(
    tmp_path: Path, fake_home: Path
) -> None:
    state, outputs = _recorded(tmp_path, fake_home)
    path = fake_home / ".claude.json"
    path.write_text(json.dumps({"numStartups": 9, "mcpServers": SERVERS}))

    assert sync.unaccepted_outputs(state, outputs) == []


def test_sync_refuses_an_unregistered_global_setup_in_process(
    tmp_path: Path, fake_home: Path
) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "loadout.toml").write_text("not even [toml\n")

    with pytest.raises(LoadoutError, match="this machine has no registered source"):
        cmd_sync(clone, "default")

    assert sorted(fake_home.iterdir()) == []


def test_an_unreadable_machine_config_registers_nothing(tmp_path: Path, fake_home: Path) -> None:
    register(tmp_path)
    machine = fake_home / ".config/loadout/config.toml"
    machine.write_text(machine.read_text() + 'profle = "x"\n')

    assert registered_source() is None


def test_the_staged_snapshot_holds_only_what_the_declared_sources_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = {
        "m": b'[[source]]\nname = "s"\npath = "."\nuse = ["skills"]\n',
        "p": b'[[source]]\nname = "e"\npath = "extra"\nuse = ["instructions"]\n',
    }
    monkeypatch.setattr(staged, "_read_blob", lambda root, entry: blobs.get(entry.oid, b""))
    names = {
        "loadout.toml": "m",
        "work.toml": "p",
        "skills/a/SKILL.md": "x",
        "agents/b.md": "x",
        "extra/instructions/i.md": "x",
        "extra/skills/c/SKILL.md": "x",
        "src/app.py": "x",
    }
    entries = {name: Entry("100644", oid) for name, oid in names.items()}

    inputs = _render_inputs(Path("/repo"), Path("."), entries)

    assert sorted(inputs) == [
        "extra/instructions/i.md",
        "loadout.toml",
        "skills/a/SKILL.md",
        "work.toml",
    ]


def test_an_owned_key_the_source_moved_past_is_accepted_by_the_record(
    tmp_path: Path, fake_home: Path
) -> None:
    """The disk holds what sync last wrote; only the record says so."""
    state, _ = _recorded(tmp_path, fake_home)
    path = fake_home / ".claude.json"
    changed = json.dumps({"mcpServers": {"docs": {"command": "docs2-mcp"}}})
    outputs = {path: Merged(frozenset({"mcpServers"}), changed, format="json")}

    assert sync.unaccepted_outputs(state, outputs) == []
