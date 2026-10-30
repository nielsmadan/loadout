"""An out-of-band edit to a key loadout owns in a shared document is a hand edit (review
2, 1.8): the guard stops sync on it, and `check` names it, while the harness's own keys
beside it stay its business."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.commands import WITHHELD_REMOVED_ABORT
from loadout.written import written_state_path
from registration import register
from test_codex_defaults import build

pytestmark = pytest.mark.integration

SHARED = "perm/claude-servers.json"


def _synced(root: Path) -> Path:
    # Forced: the fixture seeds perm/opencode.json with a co-owner's key, which the guard
    # rightly refuses on a first sync.
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    return root / SHARED


def _edit(path: Path, change: dict[str, object]) -> None:
    document = json.loads(path.read_text())
    path.write_text(json.dumps({**document, **change}, indent=2))


def test_sync_stops_on_an_edit_to_an_owned_key(root: Path, capsys) -> None:
    register(root)
    shared = _synced(root)
    _edit(shared, {"mcpServers": {"mine": {"type": "http", "url": "https://mine.example"}}})
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 1

    assert f"WARNING: {SHARED} was modified outside loadout" in capsys.readouterr().err
    assert "mine.example" in shared.read_text()


def test_check_names_an_edit_to_an_owned_key(root: Path, capsys) -> None:
    register(root)
    shared = _synced(root)
    _edit(shared, {"mcpServers": {}})
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert {"path": str(shared), "kind": "owned-key"} in json.loads(capsys.readouterr().out)[
        "drift"
    ]


def test_an_edit_to_a_key_the_harness_owns_passes(root: Path, capsys) -> None:
    register(root)
    shared = _synced(root)
    _edit(shared, {"numStartups": 7})

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads(shared.read_text())["numStartups"] == 7


def test_a_source_change_since_the_last_sync_is_not_an_edit(root: Path) -> None:
    register(root)
    shared = _synced(root)
    servers = root / "mcp.toml"
    servers.write_text(
        servers.read_text().replace("https://svc.example/mcp", "https://svc2.example")
    )

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads(shared.read_text())["mcpServers"]["svc"]["url"] == "https://svc2.example"


def test_the_committed_render_accounts_for_the_owned_keys_without_a_record(
    root: Path, capsys
) -> None:
    """With the record gone, the owned keys on disk match only the committed source's
    render: a source edit since, not a hand edit."""
    register(root)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    shared = _synced(root)
    written_state_path(root).unlink()
    servers = root / "mcp.toml"
    servers.write_text(
        servers.read_text().replace("https://svc.example/mcp", "https://svc2.example")
    )

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads(shared.read_text())["mcpServers"]["svc"]["url"] == "https://svc2.example"


def _codex(tmp_path: Path, fake_home: Path, fragment: dict[str, object], existing: str) -> Path:
    build(tmp_path, fragment)
    target = fake_home / ".codex/config.toml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(existing)
    return target


def _fragment(root: Path, fragment: dict[str, object]) -> None:
    (root / "defaults/codex.json").write_text(json.dumps(fragment))


def test_a_removed_key_the_harness_writes_back_is_stripped_again(
    tmp_path: Path, fake_home: Path
) -> None:
    """G1: a `$remove` key is owned only to evict it, so its coming back is no edit."""
    register(tmp_path)
    fragment = {"model": "gpt-5", "$remove": ["developer_instructions"]}
    target = _codex(tmp_path, fake_home, fragment, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text('developer_instructions = "x"\n' + target.read_text())

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert tomllib.loads(target.read_text()) == {"model": "gpt-5"}


def test_a_removed_key_the_harness_wrote_back_never_blocks_retiring_its_document(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 4, batch 2.1: the evicted key has no recorded hash, so retirement judged its
    value the harness wrote back as an edit to an owned key."""
    register(tmp_path)
    fragment = {"model": "gpt-5", "$remove": ["developer_instructions"]}
    target = _codex(tmp_path, fake_home, fragment, 'tui = "x"\n')
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text('developer_instructions = "x"\n' + target.read_text())
    (tmp_path / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[claude]\n')
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path), "--json"]) == 1
    assert {"path": str(target), "kind": "orphaned"} in json.loads(capsys.readouterr().out)["drift"]
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert tomllib.loads(target.read_text()) == {"tui": "x"}


def test_moving_a_removed_key_to_a_managed_value_is_opting_it_in(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, batch 2.1: the evicted key was recorded as ABSENT, so the value the harness
    wrote back read as a hand edit once the source began managing it."""
    register(tmp_path)
    target = _codex(
        tmp_path, fake_home, {"model": "gpt-5", "$remove": ["developer_instructions"]}, ""
    )
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text('developer_instructions = "x"\n' + target.read_text())
    _fragment(tmp_path, {"model": "gpt-5", "developer_instructions": "y"})
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert f'now managed: developer_instructions in {target} (was "x")' in capsys.readouterr().out
    assert tomllib.loads(target.read_text())["developer_instructions"] == "y"


def test_opting_a_key_in_overwrites_its_value_and_says_so(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """G2: a key the record never covered is a source change, not a hand edit."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, 'model_reasoning_effort = "low"\n')
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    _fragment(tmp_path, {"model": "gpt-5", "model_reasoning_effort": "high"})
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert f'now managed: model_reasoning_effort in {target} (was "low")' in (
        capsys.readouterr().out
    )
    assert tomllib.loads(target.read_text())["model_reasoning_effort"] == "high"


def test_a_record_from_before_owned_hashes_refuses_an_unexplained_value(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """G3, narrowed by review 3, 1.2: with no per-key baseline and no committed source,
    a source change cannot be told from a hand edit, so it is refused as `unknown`."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    record = written_state_path(tmp_path)
    document = json.loads(record.read_text())
    for entry in document["written"].values():
        entry.pop("owned_sha256", None)
        entry.pop("norm_sha256", None)
    record.write_text(json.dumps(document))
    _fragment(tmp_path, {"model": "gpt-6"})
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path), "--json"]) == 1
    assert {"path": str(target), "kind": "unknown"} in json.loads(capsys.readouterr().out)["drift"]
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 1

    assert tomllib.loads(target.read_text())["model"] == "gpt-5"


def test_an_orphan_from_before_owned_hashes_still_judges_every_owned_key(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """With no per-key hash to skip by, every key it owns is judged against the variants."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, 'tui = "x"\n')
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    record = written_state_path(tmp_path)
    document = json.loads(record.read_text())
    for entry in document["written"].values():
        entry.pop("owned_sha256", None)
    record.write_text(json.dumps(document))
    target.write_text(target.read_text().replace("gpt-5", "mine"))
    (tmp_path / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[claude]\n')
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 1

    assert (
        f"WARNING: {target} was modified outside loadout and is no longer rendered (unknown)"
        in (capsys.readouterr().err)
    )
    assert tomllib.loads(target.read_text())["model"] == "mine"


def test_deleting_every_server_is_flagged_rather_than_written_back(root: Path, capsys) -> None:
    """G4a: removing the last server out of band is an edit like any other (ADR 0030)."""
    register(root)
    shared = _synced(root)
    document = json.loads(shared.read_text())
    del document["mcpServers"]
    shared.write_text(json.dumps(document, indent=2))
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1
    assert {"path": str(shared), "kind": "owned-key-removed"} in json.loads(
        capsys.readouterr().out
    )["drift"]
    assert loadout.main(["sync", "--root", str(root)]) == 1
    assert "mcpServers" not in json.loads(shared.read_text())


def test_one_key_removed_while_the_source_changed_is_named_as_removed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """G4b: each key is judged alone, so the changed one passes and the gone one shows."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5", "model_verbosity": "low"}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text(target.read_text().replace('model_verbosity = "low"\n', ""))
    _fragment(tmp_path, {"model": "gpt-6", "model_verbosity": "low"})
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(target), "kind": "owned-key-removed"}
    ]


def test_a_value_changing_type_is_an_edit(tmp_path: Path, fake_home: Path, capsys) -> None:
    """G5: `true == 1` in Python, so the comparison is of canonical JSON."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"hide_agent_reasoning": True}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text(target.read_text().replace("true", "1"))

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 1

    assert "hide_agent_reasoning = 1" in target.read_text()


def _diff(err: str) -> list[str]:
    return [
        line
        for line in err.splitlines()
        if line[:1] in {"-", "+"} and line[:3] not in {"---", "+++"}
    ]


def test_the_sync_abort_shows_the_document_diff_check_shows(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, batch 2.11: the abort showed a sorted JSON projection, even of a TOML file."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text(target.read_text().replace('"gpt-5"', '"gpt-4"'))
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(tmp_path)]) == 1
    checked = _diff(capsys.readouterr().err)

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 1

    err = capsys.readouterr().err
    assert f"WARNING: {target} was modified outside loadout (owned-key)" in err
    assert _diff(err) == checked == ['-model = "gpt-4"', '+model = "gpt-5"']
    assert "Sync aborted — the '-' lines would be lost." in err


def test_the_sync_abort_for_a_removed_key_says_it_would_be_written_back(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text(target.read_text().replace('model = "gpt-5"\n', ""))
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 1

    err = capsys.readouterr().err
    assert f"WARNING: {target} was modified outside loadout (owned-key-removed)" in err
    assert _diff(err) == ['+model = "gpt-5"']
    assert (
        "Sync aborted — the '+' lines were removed outside loadout and would be written back. "
        "Remove them from the source with /loadout, or run `loadout sync --force` to write "
        "them back." in err
    )
    assert err.count("Sync aborted") == 1


def test_a_removed_key_in_a_file_holding_a_secret_is_described_without_lines(
    root: Path, capsys
) -> None:
    """ADR 0032: the diff is withheld, so there are no '+' lines to point at."""
    register(root)
    shared = _synced(root)
    document = json.loads(shared.read_text())
    del document["mcpServers"]
    shared.write_text(json.dumps(document, indent=2))
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"WARNING: {SHARED} was modified outside loadout (owned-key-removed)" in err
    assert "diff not shown because the file holds a secret" in err
    assert err.endswith(f"\n{WITHHELD_REMOVED_ABORT}\n")


def test_with_no_record_a_removed_key_written_back_is_stripped_without_a_note(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 4, batch 4.1: an evicted key is never judged, record or no record."""
    register(tmp_path)
    fragment = {"model": "gpt-5", "$remove": ["developer_instructions"]}
    target = _codex(tmp_path, fake_home, fragment, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    written_state_path(tmp_path).unlink()
    target.write_text('developer_instructions = "x"\n' + target.read_text())
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert tomllib.loads(target.read_text()) == {"model": "gpt-5"}
    assert [line for line in capsys.readouterr().out.splitlines() if "now managed" in line] == []


def test_a_newly_managed_key_named_as_a_secret_never_shows_its_value(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The value reads as plain text; only the key's name marks it."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, 'api_token = "plainvalue1"\n')
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    _fragment(tmp_path, {"model": "gpt-5", "api_token": "theirs"})
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert f"now managed: api_token in {target} (was another value)" in capsys.readouterr().out


def test_a_record_from_before_owned_hashes_names_no_key_newly_managed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """With no per-key entry the guard has judged every key, so none is new to it."""
    register(tmp_path)
    target = _codex(tmp_path, fake_home, {"model": "gpt-5"}, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    record = written_state_path(tmp_path)
    document = json.loads(record.read_text())
    for entry in document["written"].values():
        entry.pop("owned_sha256", None)
    record.write_text(json.dumps(document))
    target.write_text('model = "mine"\n')
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path), "--force"]) == 0

    out = capsys.readouterr().out
    assert f"wrote {target}" in out
    assert [line for line in out.splitlines() if "now managed" in line] == []


def test_a_removed_key_written_back_is_never_called_newly_managed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 6, batch 3 (gd-newly): sync strips it, which manages nothing new."""
    register(tmp_path)
    fragment = {"model": "gpt-5", "$remove": ["developer_instructions"]}
    target = _codex(tmp_path, fake_home, fragment, "")
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    target.write_text('developer_instructions = "x"\n' + target.read_text())
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0

    assert [line for line in capsys.readouterr().out.splitlines() if "now managed" in line] == []
