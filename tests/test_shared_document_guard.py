"""The guard on a document loadout shares with a harness fails closed (review 3, 1.1 and
1.2): with no per-key record an owned key on disk is judged against what loadout could have
written, and a merged orphan's keys are judged before retirement strips them."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.written import written_state_path
from registration import register
from test_codex_defaults import build

pytestmark = pytest.mark.integration

MINE = {"type": "stdio", "command": "mine-mcp"}


def _source(root: Path, mcp: bool = True) -> Path:
    root.mkdir(exist_ok=True)
    (root / "mcp.toml").write_text('[docs]\ntransport = "stdio"\ncommand = "docs-mcp"\n')
    (root / "loadout.toml").write_text(
        f'[[source]]\nname = "s"\npath = "."\n\n[claude]\nmcp = {str(mcp).lower()}\n'
    )
    return register(root)


def _servers(fake_home: Path) -> dict[str, object]:
    servers = json.loads((fake_home / ".claude.json").read_text())["mcpServers"]
    assert isinstance(servers, dict)
    return servers


def _hand_add(fake_home: Path) -> None:
    document = json.loads((fake_home / ".claude.json").read_text())
    document["mcpServers"]["mine"] = MINE
    (fake_home / ".claude.json").write_text(json.dumps(document, indent=2) + "\n")


def _drift(root: Path, capsys) -> list[dict[str, str]]:
    capsys.readouterr()
    loadout.main(["check", "--root", str(root), "--json"])
    drift = json.loads(capsys.readouterr().out)["drift"]
    assert isinstance(drift, list)
    return drift


def test_a_hand_added_server_survives_retiring_its_document(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, 1.1: dropping the slice retired `~/.claude.json`'s servers, the hand-added
    one with them, and check had said only "orphaned"."""
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _hand_add(fake_home)
    _source(root, mcp=False)
    document = fake_home / ".claude.json"

    assert _drift(root, capsys) == [{"path": str(document), "kind": "owned-key"}]
    assert loadout.main(["sync", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"WARNING: {document} was modified outside loadout and is no longer rendered" in err
    # The diff is of what retirement would leave: every server gone, the hand-added one too.
    lines = err.splitlines()
    assert any(line.startswith("-") and "mine-mcp" in line for line in lines)
    assert "+{}" in lines
    assert _servers(fake_home)["mine"] == MINE


def test_an_untouched_merged_orphan_is_still_retired(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _source(root, mcp=False)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert f"retired {fake_home / '.claude.json'}" in capsys.readouterr().out
    assert json.loads((fake_home / ".claude.json").read_text()) == {}


def _forget(root: Path, how: str) -> None:
    record = written_state_path(root)
    if how == "deleted":
        record.unlink()
        return
    document = json.loads(record.read_text())
    for entry in document["written"].values():
        entry.pop("owned_sha256", None)
    record.write_text(json.dumps(document))


@pytest.mark.parametrize("how", ["deleted", "legacy"])
def test_without_a_per_key_record_a_hand_added_server_is_refused(
    tmp_path: Path, fake_home: Path, capsys, how: str
) -> None:
    """Review 3, 1.2: with no baseline the guard was off, and sync overwrote it."""
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _forget(root, how)
    _hand_add(fake_home)

    assert _drift(root, capsys) == [{"path": str(fake_home / ".claude.json"), "kind": "unknown"}]
    assert loadout.main(["sync", "--root", str(root)]) == 1

    assert _servers(fake_home)["mine"] == MINE


def test_a_first_sync_refuses_servers_already_in_the_shared_document(
    tmp_path: Path, fake_home: Path
) -> None:
    (fake_home / ".claude.json").write_text(json.dumps({"mcpServers": {"mine": MINE}}))
    root = _source(tmp_path / "source")

    assert loadout.main(["sync", "--root", str(root)]) == 1

    assert _servers(fake_home) == {"mine": MINE}


def test_a_first_sync_adds_a_key_the_shared_document_lacks(tmp_path: Path, fake_home: Path) -> None:
    """Writing a key the file does not hold loses nothing, so no baseline is needed."""
    (fake_home / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    root = _source(tmp_path / "source")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    document = json.loads((fake_home / ".claude.json").read_text())
    assert (document["numStartups"], list(document["mcpServers"])) == (3, ["docs"])


def test_without_a_record_the_committed_render_explains_the_keys(
    tmp_path: Path, fake_home: Path
) -> None:
    """The committed source's render is a baseline: what it wrote is no hand edit."""
    root = _source(tmp_path / "source")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _forget(root, "legacy")
    servers = root / "mcp.toml"
    servers.write_text(servers.read_text().replace("docs-mcp", "docs2-mcp"))

    assert loadout.main(["sync", "--root", str(root)]) == 0

    docs = _servers(fake_home)["docs"]
    assert isinstance(docs, dict)
    assert docs["command"] == "docs2-mcp"


def test_an_orphan_whose_keys_were_deleted_by_hand_is_retired(
    tmp_path: Path, fake_home: Path
) -> None:
    """Nothing is lost stripping a key that is no longer there."""
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (fake_home / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    _source(root, mcp=False)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert json.loads((fake_home / ".claude.json").read_text()) == {"numStartups": 3}


def test_a_legacy_orphan_the_committed_render_explains_is_retired(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _source(tmp_path / "source")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    assert loadout.main(["sync", "--root", str(root)]) == 0
    _forget(root, "legacy")
    _source(root, mcp=False)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert f"retired {fake_home / '.claude.json'}" in capsys.readouterr().out


def test_a_key_back_at_its_committed_value_is_no_hand_edit_with_a_record(
    tmp_path: Path, fake_home: Path
) -> None:
    """Review 3, batch 5.4: with the record kept, a key on disk matching neither this
    render nor the record is still explained by the committed source's render."""
    root = _source(tmp_path / "source")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    assert loadout.main(["sync", "--root", str(root)]) == 0
    committed = (fake_home / ".claude.json").read_text()
    servers = root / "mcp.toml"
    servers.write_text(servers.read_text().replace("docs-mcp", "docs2-mcp"))
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (fake_home / ".claude.json").write_text(committed)

    assert loadout.main(["sync", "--root", str(root)]) == 0

    docs = _servers(fake_home)["docs"]
    assert isinstance(docs, dict)
    assert docs["command"] == "docs2-mcp"


def test_a_harness_reordering_the_keys_inside_an_owned_map_is_no_edit(
    tmp_path: Path, fake_home: Path
) -> None:
    """Review 3, batch 5.6: a key's hash is of its value in one spelling, whatever order
    the harness writes the map's keys back in."""
    root = _source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = json.loads((fake_home / ".claude.json").read_text())
    document["mcpServers"] = {
        name: dict(reversed(list(server.items())))
        for name, server in document["mcpServers"].items()
    }
    assert list(document["mcpServers"]["docs"]) != list(_servers(fake_home)["docs"])
    (fake_home / ".claude.json").write_text(json.dumps(document, indent=2) + "\n")

    assert loadout.main(["sync", "--root", str(root)]) == 0


def test_a_removal_beside_an_edit_is_named_an_edit(tmp_path: Path, fake_home: Path, capsys) -> None:
    """Review 3, batch 5.6: removed only when every unexplained key is gone."""
    root = register(build(tmp_path, {"model": "gpt-5", "tui": {"theme": "dark"}}))
    target = fake_home / ".codex/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text("")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    target.write_text('model = "gpt-4"\n')
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(target), "kind": "owned-key"}
    ]
