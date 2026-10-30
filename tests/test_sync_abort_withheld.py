"""Sync's refusal over a file whose diff it withholds, because the file holds a secret.

It said "the '-' lines would be lost" though no lines were shown (ADR 0032).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.commands import WITHHELD_ABORT

pytestmark = pytest.mark.integration

SECRET = "Deploy with API_KEY=sk-live-0123456789abcdef.\n"


def _edited(root: Path, rules: str) -> None:
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (root / "loadout/instructions/main.md").write_text(rules)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    agents = root / "AGENTS.md"
    agents.write_text(agents.read_text() + "A line added by hand.\n")


def _refusal(root: Path, capsys) -> str:
    capsys.readouterr()
    assert loadout.main(["sync", "--root", str(root)]) == 1
    return capsys.readouterr().err


def test_a_withheld_diff_is_described_as_unshown(tmp_path: Path, capsys) -> None:
    _edited(tmp_path / "p", SECRET)

    err = _refusal(tmp_path / "p", capsys)

    assert "diff not shown because the file holds a secret" in err
    assert err.endswith(f"\n{WITHHELD_ABORT}\n")


def test_a_shown_diff_keeps_the_minus_lines_sentence(tmp_path: Path, capsys) -> None:
    _edited(tmp_path / "p", "Be brief.\n")

    err = _refusal(tmp_path / "p", capsys)

    assert "-A line added by hand." in err
    assert "Sync aborted — the '-' lines would be lost." in err
