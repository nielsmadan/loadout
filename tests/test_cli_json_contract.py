"""Under `--json`, init prints one JSON object whatever happens (review 2, 2.13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout import cli, init_workflow


def _one_object(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    document = json.loads(capsys.readouterr().out)
    assert isinstance(document, dict)
    return document


def test_an_argument_error_still_prints_the_json_object(tmp_path: Path, capsys) -> None:
    assert loadout.main(["init", "project", "--root", str(tmp_path), "--json", "--bogus"]) == 2

    document = _one_object(capsys)
    assert (document["status"], document["exit_code"]) == ("error", 2)
    assert "--bogus" in str(document["error"])


def test_an_unknown_harness_is_a_usage_error_that_names_it(tmp_path: Path, capsys) -> None:
    argv = ["init", "project", "--root", str(tmp_path), "--harness", "bogus", "--json"]
    assert loadout.main(argv) == 2

    document = _one_object(capsys)
    assert (document["status"], document["exit_code"]) == ("error", 2)
    assert "'bogus'" in str(document["error"])


def test_a_crash_still_prints_the_json_object(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*args: object, **kwargs: object) -> int:
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_init", crash)
    assert loadout.main(["init", "project", "--root", str(tmp_path), "--json"]) == 4

    document = _one_object(capsys)
    assert (document["status"], document["exit_code"]) == ("error", 4)
    assert "boom" in str(document["error"])


def test_every_envelope_names_its_message_error(capsys) -> None:
    """The interrupted envelope said `message` where every other said `error`."""
    assert "error" in init_workflow.interrupted_document("/backups/x")
    assert "message" not in init_workflow.interrupted_document("/backups/x")
