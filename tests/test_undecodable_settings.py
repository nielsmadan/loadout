"""A settings document that is not UTF-8 is named with exit 3, never a traceback
(review 5, batch 2.3: round 4's 1.5 covered only the documents loadout shares)."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.notices import _known_marketplaces
from registration import register

BROKEN = b'{"model": "op\xc3'


def _project(root: Path) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    return root


@pytest.mark.integration
@pytest.mark.parametrize("command", [["sync"], ["sync", "--force"], ["check"]])
def test_a_project_settings_source_that_is_not_utf_8_is_named(
    tmp_path: Path, capsys, command: list[str]
) -> None:
    """The generated `.claude/settings.json` is drift instead: test_project_unparsable_documents."""
    root = _project(tmp_path / "p")
    path = root / "loadout/settings/claude.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(BROKEN)
    capsys.readouterr()

    assert loadout.main([*command, "--root", str(root)]) == 3

    assert capsys.readouterr().err.startswith(f"loadout: {path}: invalid JSON:")


@pytest.mark.integration
def test_an_existing_output_whose_keys_are_preserved_that_is_not_utf_8_is_named(
    root: Path, capsys
) -> None:
    register(root)
    output = root / "perm/opencode.json"
    output.write_bytes(BROKEN)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 3

    assert f"loadout: {output}: invalid JSON:" in capsys.readouterr().err


def test_a_marketplace_registry_that_is_not_utf_8_registers_nothing(tmp_path: Path) -> None:
    registry = tmp_path / "plugins/known_marketplaces.json"
    registry.parent.mkdir()
    registry.write_bytes(BROKEN)

    assert _known_marketplaces("claude", {}, {"CLAUDE_CONFIG_DIR": str(tmp_path)}) == frozenset()
