"""A document loadout shares with a harness that no longer parses is named, reported by
check as its own kind, and replaced only by `sync --force` (review 3, 1.6)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration

CASES = {
    "json": ("[claude]\nmcp = true\n", ".claude.json", '{"mcpServers": {"do'),
    "toml": ("[codex]\nmcp = true\n", ".codex/config.toml", "[mcp_servers.docs\ncommand = \n"),
    # Review 3, batch 2.10: these parse, but cannot hold loadout's keys.
    "json-array": ("[claude]\nmcp = true\n", ".claude.json", "[]\n"),
    "toml-scalar": ('[codex]\ndefaults = "codex"\n', ".codex/config.toml", "tui = 5\n"),
}


def _format(case: str) -> str:
    return case.split("-", maxsplit=1)[0].upper()


def _source(root: Path, agent_block: str) -> Path:
    root.mkdir()
    (root / "mcp.toml").write_text('[docs]\ntransport = "stdio"\ncommand = "docs-mcp"\n')
    (root / "defaults").mkdir()
    (root / "defaults/codex.json").write_text(json.dumps({"tui": {"theme": "docs-mcp"}}))
    (root / "loadout.toml").write_text(f'[[source]]\nname = "s"\npath = "."\n\n{agent_block}')
    return register(root)


def _truncated(tmp_path: Path, fake_home: Path, case: str) -> tuple[Path, Path]:
    block, relative, broken = CASES[case]
    root = _source(tmp_path / "source", block)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = fake_home / relative
    document.write_text(broken)
    return root, document


@pytest.mark.parametrize("case", sorted(CASES))
def test_check_names_an_unparsable_document_as_its_own_kind(
    tmp_path: Path, fake_home: Path, capsys, case: str
) -> None:
    root, document = _truncated(tmp_path, fake_home, case)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(document), "kind": "unparsable"}
    ]


@pytest.mark.parametrize("case", sorted(CASES))
def test_sync_refuses_an_unparsable_document_and_names_the_remedy(
    tmp_path: Path, fake_home: Path, capsys, case: str
) -> None:
    root, document = _truncated(tmp_path, fake_home, case)
    broken = document.read_text()
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {document} is not a {_format(case)} document loadout can write its keys "
        "into; fix or delete it, then run `loadout sync`, or run `loadout sync --force` to "
        "replace it\n"
    )
    assert document.read_text() == broken


@pytest.mark.parametrize("case", sorted(CASES))
def test_sync_force_replaces_an_unparsable_document(
    tmp_path: Path, fake_home: Path, case: str
) -> None:
    root, document = _truncated(tmp_path, fake_home, case)

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0

    assert "docs-mcp" in document.read_text()
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_check_advises_the_remedy_for_an_unparsable_document(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root, document = _truncated(tmp_path, fake_home, "json")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"DRIFT (unparsable): {document}" in err
    assert "  1 unparsable: a file loadout shares with a harness no longer parses; fix or" in err


def _unparsable_orphan(tmp_path: Path, fake_home: Path) -> tuple[Path, Path]:
    root, document = _truncated(tmp_path, fake_home, "json")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\nmcp = false\n'
    )
    return root, document


def test_check_reports_an_unparsable_orphan(tmp_path: Path, fake_home: Path, capsys) -> None:
    """Review 3, gap (c): stripping loadout's keys from it raised a traceback."""
    root, document = _unparsable_orphan(tmp_path, fake_home)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(document), "kind": "unparsable"}
    ]


@pytest.mark.parametrize("force", [[], ["--force"]])
def test_sync_refuses_an_unparsable_orphan(
    tmp_path: Path, fake_home: Path, capsys, force: list[str]
) -> None:
    root, document = _unparsable_orphan(tmp_path, fake_home)
    broken = document.read_text()
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root), *force]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {document} is not a JSON document loadout can remove its keys from; fix or "
        "delete it, then run `loadout sync`\n"
    )
    assert document.read_text() == broken
