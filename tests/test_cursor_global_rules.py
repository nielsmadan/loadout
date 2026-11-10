"""Cursor's global instructions: a rule in ~/.cursor/rules, which its rules loader
reads as an ancestor of every workspace under the home directory."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.composition import HEADER
from loadout.emit import render_global
from loadout.errors import LoadoutError
from registration import register

SOURCE = '[[source]]\nname = "test"\npath = "."\n'
RULE = "---\nalwaysApply: true\n---\n\n"


def build(root: Path, body: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "loadout.toml").write_text(SOURCE + body, encoding="utf-8")
    fragments = root / "instructions"
    fragments.mkdir(exist_ok=True)
    (fragments / "intro.md").write_text("intro\n", encoding="utf-8")
    (fragments / "style.md").write_text("style\n", encoding="utf-8")
    return root


def rendered(root: Path) -> dict[str, object]:
    return {str(path): output for path, output in render_global(root).items()}


def test_cursor_instructions_are_a_rule_in_the_home_cursor_folder(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    root = build(tmp_path, '\n[cursor]\ninstructions = ["intro", "style"]\n')
    outputs = rendered(root)
    rule = outputs[str(fake_home / ".cursor" / "rules" / "loadout.mdc")]
    assert rule == f"{RULE}{HEADER}\n\nintro\n\nstyle\n"


def test_the_rule_stays_in_the_home_folder_when_cursor_config_dir_moves(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CURSOR_CONFIG_DIR", str(tmp_path / "elsewhere"))
    register(tmp_path)
    root = build(tmp_path, '\n[cursor]\ninstructions = ["intro"]\n')
    assert str(fake_home / ".cursor" / "rules" / "loadout.mdc") in rendered(root)


def test_an_mdc_destination_gets_the_frontmatter_and_a_markdown_one_does_not(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    root = build(
        tmp_path,
        '\n[instructions.shared]\norder = ["intro"]\n'
        'destinations = ["~/.cursor/rules/shared.mdc", "~/notes/shared.md"]\n',
    )
    outputs = rendered(root)
    document = f"{HEADER}\n\nintro\n"
    assert outputs[str(fake_home / ".cursor" / "rules" / "shared.mdc")] == RULE + document
    assert outputs[str(fake_home / "notes" / "shared.md")] == document


def test_cursor_instructions_claim_their_path_against_another_target(
    tmp_path: Path, fake_home: Path
) -> None:
    register(tmp_path)
    root = build(
        tmp_path,
        '\n[cursor]\ninstructions = ["intro"]\n\n[instructions.mine]\norder = ["style"]\n'
        'destinations = ["~/.cursor/rules/loadout.mdc"]\n',
    )
    with pytest.raises(LoadoutError, match=r"loadout\.mdc"):
        render_global(root)
