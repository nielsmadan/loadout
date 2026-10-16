from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.emit import render_global
from loadout.harnesses import GLOBAL_PRESET
from registration import register

SOURCE = '[[source]]\nname = "test"\npath = "."\n'


def _source(root: Path, agent: str, permissions: str | None) -> Path:
    root.mkdir()
    (root / "loadout.toml").write_text(f"{SOURCE}\n[{agent}]\n", encoding="utf-8")
    (root / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://jina.example"\n', encoding="utf-8"
    )
    if permissions is not None:
        (root / "permissions.toml").write_text(permissions, encoding="utf-8")
    return root


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_a_global_source_without_permissions_renders_as_an_empty_one(
    tmp_path: Path, agent: str
) -> None:
    absent = render_global(register(_source(tmp_path / "absent", agent, None)))
    empty = render_global(register(_source(tmp_path / "empty", agent, "")))

    assert absent == empty
    assert absent


@pytest.mark.migration_integration
def test_a_codex_only_global_init_and_the_sync_after_it_succeed(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    (fake_home / ".codex").mkdir()
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0

    assert not (source / "loadout" / "permissions.toml").exists()
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert "Up to date." in capsys.readouterr().out
