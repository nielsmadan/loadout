"""A generated text output that is not UTF-8 is judged by the guard's rules like any
other hand edit, shown leniently, and never crashes a command (review 4, batch 1.5)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.emit import render_all
from loadout.sync import compare_with_disk
from loadout.written import written_state_path
from registration import register

pytestmark = pytest.mark.integration

BROKEN = b"Rules \xc3\n"


def _synced(tmp_path: Path, fake_home: Path) -> tuple[Path, Path]:
    root = register(tmp_path / "source")
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("Rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'
    )
    assert loadout.main(["sync", "--root", str(root)]) == 0
    output = fake_home / "out.md"
    output.write_bytes(BROKEN)
    return root, output


@pytest.mark.parametrize(("recorded", "kind"), [(True, "hand-edited"), (False, "unknown")])
def test_check_classifies_an_undecodable_output_by_the_guard(
    tmp_path: Path, fake_home: Path, capsys, recorded: bool, kind: str
) -> None:
    root, output = _synced(tmp_path, fake_home)
    if not recorded:
        written_state_path(root).unlink()
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [{"path": str(output), "kind": kind}]


def test_check_shows_an_undecodable_output_leniently(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root, output = _synced(tmp_path, fake_home)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"DRIFT (hand-edited): {output}" in err
    assert "-Rules �\n" in err


def test_sync_refuses_an_undecodable_output_and_keeps_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root, output = _synced(tmp_path, fake_home)
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 1

    assert (
        f"WARNING: {output} was modified outside loadout (hand-edited)" in capsys.readouterr().err
    )
    assert output.read_bytes() == BROKEN


def test_sync_force_replaces_an_undecodable_output(tmp_path: Path, fake_home: Path) -> None:
    root, output = _synced(tmp_path, fake_home)

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0

    assert output.read_text().endswith("Rules.\n")
    assert loadout.main(["check", "--root", str(root)]) == 0


def _orphaned(root: Path) -> None:
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/other.md"]\norder = ["x"]\n'
    )


def test_an_undecodable_orphan_is_reported_refused_and_forced_away(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root, output = _synced(tmp_path, fake_home)
    _orphaned(root)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1
    assert {"path": str(output), "kind": "hand-edited"} in json.loads(capsys.readouterr().out)[
        "drift"
    ]
    assert loadout.main(["sync", "--root", str(root)]) == 1
    assert output.read_bytes() == BROKEN
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    assert not output.exists()


def test_the_preview_shows_an_undecodable_output_as_an_update(
    tmp_path: Path, fake_home: Path
) -> None:
    root, output = _synced(tmp_path, fake_home)

    changes = {c.path: c for c in compare_with_disk(render_all(root))}

    assert (changes[output].kind, changes[output].actual) == ("update", "Rules �\n")
