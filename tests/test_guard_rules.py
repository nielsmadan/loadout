"""The guard, the registration gate and the staged narrowing, called as functions so
`just check` holds them too: until now only the integration tier did (review 3, batch 5.7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

import pytest

from loadout import machine, sync, written
from loadout.commands import DRIFT_ADVICE, _abort_sentences, cmd_sync
from loadout.emit import RenderContext, refuse_unregistered_global
from loadout.errors import LoadoutError
from loadout.files import DriftKind, UnparsableDocument, refuse_unparsable
from loadout.git_tree import Entry, and_above, select_inputs
from loadout.machine import Registration, registered_at, registration, unregistered
from loadout.output import Merged
from loadout.written import (
    every_recorded_path,
    merged_entry,
    record_written,
    text_entry,
    written_state_path,
)
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
    merged = Merged(frozenset({"mcpServers"}), document, format="json")
    record_written(root, {path: merged_entry(merged)})
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

    assert registration().source is None


def test_the_staged_snapshot_holds_only_what_the_declared_sources_use() -> None:
    blobs = {
        "m": b'[[source]]\nname = "s"\npath = "."\nuse = ["skills"]\n',
        "p": b'[[source]]\nname = "e"\npath = "extra"\nuse = ["instructions"]\n',
    }
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

    inputs = select_inputs(Path("."), entries, blobs)

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


def test_the_refusal_names_the_registration_that_decided_it(
    tmp_path: Path, fake_home: Path
) -> None:
    """Review 4, batch 2.10: the message read the machine config again, so a context's
    registration decided and the real config, here none, was named."""
    root = tmp_path / "clone"
    root.mkdir()
    (root / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    other = tmp_path / "other"
    context = RenderContext(registration=registered_at(other))

    with pytest.raises(LoadoutError, match=f"registered source is {other}; to make this folder"):
        refuse_unregistered_global(root, context)


def test_a_staged_entry_is_checked_with_every_folder_above_it() -> None:
    assert and_above(".claude/skills/x/SKILL.md") == [
        ".claude/skills/x/SKILL.md",
        ".claude/skills/x",
        ".claude/skills",
        ".claude",
    ]


def test_every_recorded_path_uses_the_record_already_read(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 4, batch 3.5: the unmanaged scan read this root's record a second time."""
    folder = fake_home / ".claude/skills"
    mine, theirs = tmp_path / "mine", tmp_path / "theirs"
    for root, name in ((mine, "a"), (theirs, "b")):
        root.mkdir()
        record_written(
            root, {folder / name / "SKILL.md": text_entry(folder / name / "SKILL.md", "x")}
        )
    read: list[Path] = []
    real = written._document

    def counting(record: Path) -> object:
        read.append(record)
        return real(record)

    monkeypatch.setattr(written, "_document", counting)
    known = {folder / "c/SKILL.md": text_entry(folder / "c/SKILL.md", "x")}

    found = every_recorded_path([folder], known_root=mine, known=known)

    assert sorted(found) == [folder / "b/SKILL.md", folder / "c/SKILL.md"]
    assert read == [written_state_path(theirs)]


def test_check_has_advice_for_every_drift_kind() -> None:
    """Review 4, batch 3.5: a kind added without advice printed nothing after its count."""
    assert sorted(DRIFT_ADVICE) == sorted(get_args(DriftKind))


def test_a_record_entry_owning_a_key_that_is_not_text_is_dropped() -> None:
    """Review 4, batch 4.1: a record only widens what the guard accepts."""
    assert (
        written._entry({"kind": "merged", "owned": [1]}),
        written._entry({"kind": "merged", "owned": ["a"]}),
    ) == (
        None,
        written.WrittenEntry(kind="merged", owned=("a",)),
    )


def test_the_registration_remedy_quotes_a_folder_with_a_space(tmp_path: Path) -> None:
    root = tmp_path / "my config"

    message = str(unregistered(root, Registration(None)))

    assert message.endswith(f"register this folder with `loadout init global '{root}'`")


# Review 4, batch 4.4: each guard rule pinned at the unit tier, which until now only the
# integration tier killed.


def test_with_no_record_an_owned_key_no_variant_explains_is_unknown(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = _shared(fake_home, {"mine": {"command": "mine-mcp"}})
    outputs = {
        path: Merged(frozenset({"mcpServers"}), json.dumps({"mcpServers": SERVERS}), format="json")
    }

    refused = sync.unaccepted_outputs(sync.sync_state(root), outputs)

    assert [(item.path, item.kind) for item in refused] == [(path, "unknown")]


def test_an_owned_key_removed_since_the_record_is_owned_key_removed(
    tmp_path: Path, fake_home: Path
) -> None:
    state, outputs = _recorded(tmp_path, fake_home)
    path = fake_home / ".claude.json"
    path.write_text(json.dumps({"numStartups": 3}))

    refused = sync.unaccepted_outputs(state, outputs)

    assert [(item.path, item.kind) for item in refused] == [(path, "owned-key-removed")]


def test_an_edited_orphan_is_refused_as_orphaned(tmp_path: Path, fake_home: Path) -> None:
    state, _ = _recorded(tmp_path, fake_home)
    path = _shared(fake_home, {"docs": {"command": "edited-mcp"}})

    refused = sync.unaccepted_outputs(state, {})

    assert [(item.path, item.kind, item.orphaned) for item in refused] == [
        (path, "owned-key", True)
    ]


def test_an_evicted_key_on_disk_is_never_judged(tmp_path: Path, fake_home: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    path = fake_home / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text('developer_instructions = "x"\nmodel = "gpt-5"\n')
    merged = Merged(
        frozenset({"model", "developer_instructions"}),
        'model = "gpt-5"\n',
        evicted=frozenset({"developer_instructions"}),
    )

    assert sync.unaccepted_outputs(sync.sync_state(root), {path: merged}) == []


def test_an_unparsable_shared_document_is_refused_by_name(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_bytes(b'{"mcpServers": {"d\xc3')

    with pytest.raises(UnparsableDocument, match="is not a JSON document loadout can write"):
        refuse_unparsable({path: Merged(frozenset({"mcpServers"}), "{}", format="json")})


def test_the_abort_says_what_each_kind_of_refused_line_means() -> None:
    assert [
        sentence.split(" — ")[1][:9]
        for sentence in _abort_sentences({"owned-key", "owned-key-removed"})
    ] == [
        "the '-' l",
        "the '+' l",
    ]


def test_a_key_the_record_never_covered_is_newly_managed(tmp_path: Path, fake_home: Path) -> None:
    state, _ = _recorded(tmp_path, fake_home)
    path = fake_home / ".claude.json"
    path.write_text(json.dumps({"mcpServers": SERVERS, "theme": "dark"}))
    document = json.dumps({"mcpServers": SERVERS, "theme": "light"})
    outputs = {path: Merged(frozenset({"mcpServers", "theme"}), document, format="json")}

    assert sync.newly_managed(state, outputs) == [(path, "theme", '"dark"')]


def test_a_refused_sync_reads_the_machine_config_once(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5, batch 3.3: the gate, the render and the guard each read it."""
    (tmp_path / "source").mkdir()
    root = register(tmp_path / "source")
    (root / "instructions").mkdir()
    (root / "instructions/x.md").write_text("Rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'
    )
    assert cmd_sync(root, "default") == 0
    (fake_home / "out.md").write_text("Hand-written.\n")
    reads: list[Path] = []
    real = machine.load_machine_config

    def counting(path: Path, **kwargs: bool) -> object:
        reads.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(machine, "load_machine_config", counting)

    assert cmd_sync(root, "default") == 1
    assert len(reads) == 1
