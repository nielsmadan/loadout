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
    "json": ("[claude]\nmcp = true\n", ".claude.json", b'{"mcpServers": {"do'),
    "toml": ("[codex]\nmcp = true\n", ".codex/config.toml", b"[mcp_servers.docs\ncommand = \n"),
    # Review 3, batch 2.10: these parse, but cannot hold loadout's keys.
    "json-array": ("[claude]\nmcp = true\n", ".claude.json", b"[]\n"),
    "toml-scalar": ('[codex]\ndefaults = "codex"\n', ".codex/config.toml", b"tui = 5\n"),
    # Review 4, batch 1.5: truncated mid-character, so not even UTF-8.
    "json-undecodable": ("[claude]\nmcp = true\n", ".claude.json", b'{"mcpServers": {"d\xc3'),
    "toml-undecodable": (
        "[codex]\nmcp = true\n",
        ".codex/config.toml",
        b'[mcp_servers.docs]\ncommand = "d\xe2\x82',
    ),
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
    document.write_bytes(broken)
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
    broken = document.read_bytes()
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {document} is not a {_format(case)} document loadout can write its keys "
        "into; fix or delete it, then run `loadout sync`, or run `loadout sync --force` to "
        "replace it\n"
    )
    assert document.read_bytes() == broken


@pytest.mark.parametrize("case", sorted(CASES))
def test_sync_force_replaces_an_unparsable_document(
    tmp_path: Path, fake_home: Path, case: str
) -> None:
    root, document = _truncated(tmp_path, fake_home, case)

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0

    assert "docs-mcp" in document.read_text()
    assert loadout.main(["check", "--root", str(root)]) == 0


@pytest.mark.parametrize("case", ["json", "json-undecodable"])
def test_check_advises_the_remedy_for_an_unparsable_document(
    tmp_path: Path, fake_home: Path, capsys, case: str
) -> None:
    root, document = _truncated(tmp_path, fake_home, case)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"DRIFT (unparsable): {document}" in err
    assert "  1 unparsable: a file loadout shares with a harness no longer parses; fix or" in err


@pytest.fixture(params=["json", "json-undecodable"])
def orphan_case(request: pytest.FixtureRequest) -> str:
    return str(request.param)


def _unparsable_orphan(tmp_path: Path, fake_home: Path, case: str) -> tuple[Path, Path]:
    root, document = _truncated(tmp_path, fake_home, case)
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n[claude]\nmcp = false\n'
    )
    return root, document


def test_check_reports_an_unparsable_orphan(
    tmp_path: Path, fake_home: Path, capsys, orphan_case: str
) -> None:
    """Review 3, gap (c): stripping loadout's keys from it raised a traceback."""
    root, document = _unparsable_orphan(tmp_path, fake_home, orphan_case)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(document), "kind": "unparsable"}
    ]


@pytest.mark.parametrize("force", [[], ["--force"]])
def test_sync_refuses_an_unparsable_orphan(
    tmp_path: Path, fake_home: Path, capsys, force: list[str], orphan_case: str
) -> None:
    root, document = _unparsable_orphan(tmp_path, fake_home, orphan_case)
    broken = document.read_bytes()
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root), *force]) == 3

    assert capsys.readouterr().err == (
        f"loadout: {document} is not a JSON document loadout can remove its keys from; fix or "
        "delete it, then run `loadout sync`\n"
    )
    assert document.read_bytes() == broken


def test_check_shows_no_diff_for_an_unparsable_orphan(
    tmp_path: Path, fake_home: Path, capsys, orphan_case: str
) -> None:
    """Review 4, batch 2.8: the whole shared document showed as deleted, which retiring
    it never does."""
    root, document = _unparsable_orphan(tmp_path, fake_home, orphan_case)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    assert capsys.readouterr().err.splitlines()[:3] == [
        f"DRIFT (unparsable): {document}",
        "",
        "1 generated file(s) out of sync:",
    ]
