"""A project settings file loadout shares with a harness that no longer parses is drift of
kind `unparsable`, and `sync --force` replaces it, as it is at global scope (review 3, 1.6).

Rendering read the file for its foreign keys first, so it stopped with "invalid JSON …
delete it and re-run" and exit 3 even under `--force`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.integration

CASES = {
    "claude": ".claude/settings.json",
    "droid": ".factory/settings.json",
    "opencode": "opencode.json",
}


BROKEN = {"truncated": b'{"permissions": {\n', "undecodable": b'{"model": "op\xc3'}


def _broken(root: Path, harness: str, broken: bytes = BROKEN["truncated"]) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text(f'harnesses = ["{harness}"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\nallow = ["git status"]\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    document = root / CASES[harness]
    document.write_bytes(broken)
    return document


@pytest.mark.parametrize("broken", sorted(BROKEN))
@pytest.mark.parametrize("harness", sorted(CASES))
def test_check_reports_an_unparsable_project_settings_file_as_its_own_kind(
    tmp_path: Path, capsys, harness: str, broken: str
) -> None:
    document = _broken(tmp_path / "p", harness, BROKEN[broken])
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(tmp_path / "p"), "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["drift"] == [
        {"path": str(document), "kind": "unparsable"}
    ]


@pytest.mark.parametrize("broken", sorted(BROKEN))
@pytest.mark.parametrize("harness", sorted(CASES))
def test_sync_refuses_it_and_force_replaces_it(
    tmp_path: Path, capsys, harness: str, broken: str
) -> None:
    root = tmp_path / "p"
    document = _broken(root, harness, BROKEN[broken])
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "or run `loadout sync --force` to replace it" in capsys.readouterr().err
    assert document.read_bytes() == BROKEN[broken]

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    assert "git status" in document.read_text()
    assert loadout.main(["check", "--root", str(root)]) == 0
