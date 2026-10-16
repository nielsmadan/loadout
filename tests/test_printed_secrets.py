"""No diff loadout prints holds a file with a secret: check, the sync abort and the file
that keeps its full diff name it and count its changed lines instead (ADR 0032), and the
"now managed" note never shows a value that looks like one (review 3, 1.7)."""

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
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n\n'
        '[instructions.other]\ndestinations = ["~/other.md"]\norder = ["x"]\n'
    )
    register(root)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    return fake_home / "out.md"


def _withheld(path: Path, count: int) -> str:
    return (
        f"{path}: {count} line(s) differ; diff not shown because the file holds a secret "
        "(a token in a provider's format) — read the file directly\n"
    )


def test_check_names_a_file_with_a_hand_added_token_instead_of_diffing_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    output = _synced(tmp_path / "source", fake_home)
    output.write_text(output.read_text() + f"push with {TOKEN}\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path / "source")]) == 1

    assert capsys.readouterr().err.splitlines(keepends=True)[:2] == [
        f"DRIFT (hand-edited): {output}\n",
        _withheld(output, 1),
    ]


def test_the_sync_abort_and_its_full_diff_name_a_file_with_a_token(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    output = _synced(tmp_path / "source", fake_home)
    output.write_text(output.read_text() + f"push with {TOKEN}\n")
    other = fake_home / "other.md"
    other.write_text(other.read_text() + "".join(f"line {index}\n" for index in range(60)))
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path / "source")]) == 1

    err = capsys.readouterr().err
    kept = (state_dir() / "sync-abort.diff").read_text()
    assert f"The complete diff is at {state_dir() / 'sync-abort.diff'}" in err
    assert _withheld(output, 1) in err.splitlines(keepends=True)
    assert _withheld(output, 1) in kept.splitlines(keepends=True)
    assert "-line 59\n" in kept


def test_a_token_past_the_scan_s_cap_still_withholds_the_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 4, batch 1.3: redaction stopped where init's 1 MiB scan did."""
    output = _synced(tmp_path / "source", fake_home)
    filler = "".join(f"line {index} {'x' * 100}\n" for index in range(11_000))
    output.write_text(output.read_text() + filler + f"push with {TOKEN}\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path / "source")]) == 1

    assert _withheld(output, 11_001) in capsys.readouterr().err.splitlines(keepends=True)


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
