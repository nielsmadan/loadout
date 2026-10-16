"""No diff loadout prints repeats a secret it can recognise: check, the sync abort and the
file that keeps its full diff, and the "now managed" note (review 3, 1.7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.machine import state_dir
from registration import register
from test_codex_defaults import build

pytestmark = pytest.mark.integration

TOKEN = "ghp_" + "a1B2" * 9


def _synced(root: Path, fake_home: Path) -> Path:
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("Rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'
    )
    register(root)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    return fake_home / "out.md"


def test_check_never_prints_a_hand_added_token(tmp_path: Path, fake_home: Path, capsys) -> None:
    output = _synced(tmp_path / "source", fake_home)
    output.write_text(output.read_text() + f"push with {TOKEN}\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path / "source")]) == 1

    captured = capsys.readouterr()
    assert "-push with <redacted>\n" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_the_sync_abort_and_its_full_diff_never_hold_a_token(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    output = _synced(tmp_path / "source", fake_home)
    lines = "".join(f"line {index}\n" for index in range(60))
    output.write_text(output.read_text() + lines + f"push with {TOKEN}\n")
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path / "source")]) == 1

    err = capsys.readouterr().err
    kept = state_dir() / "sync-abort.diff"
    assert f"The complete diff is at {kept}" in err
    assert "-push with <redacted>\n" in kept.read_text()
    assert TOKEN not in err + kept.read_text()


def test_a_token_past_the_scan_s_cap_never_lands_in_the_abort_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 4, batch 1.3: redaction stopped where the 1 MiB scan did."""
    output = _synced(tmp_path / "source", fake_home)
    filler = "".join(f"line {index} {'x' * 100}\n" for index in range(11_000))
    output.write_text(output.read_text() + filler + f"push with {TOKEN}\n")
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path / "source")]) == 1

    kept = (state_dir() / "sync-abort.diff").read_text()
    assert "-push with <redacted>\n" in kept
    assert TOKEN not in capsys.readouterr().err + kept


def test_a_successful_sync_removes_the_last_abort_diff(tmp_path: Path, fake_home: Path) -> None:
    root = tmp_path / "source"
    _synced(root, fake_home)
    kept = state_dir() / "sync-abort.diff"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("an earlier abort\n")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert not kept.exists()


def test_now_managed_never_shows_a_value_that_looks_like_a_secret(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = register(build(tmp_path, {"model": "gpt-5"}))
    target = fake_home / ".codex/config.toml"
    target.parent.mkdir(parents=True)
    target.write_text(f'model_provider = "{TOKEN}"\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / "defaults/codex.json").write_text(
        json.dumps({"model": "gpt-5", "model_provider": "openai"})
    )
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 0

    out = capsys.readouterr().out
    assert f"now managed: model_provider in {target} (was another value)" in out
    assert TOKEN not in out
