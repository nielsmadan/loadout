"""Under `--json`, init prints one JSON object whatever happens (review 2, 2.13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout import cli, init_workflow
from loadout.migration_backup import Backup


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
    document = init_workflow.interrupted_document(Backup(Path("/backups/x")))
    assert set(document) == {"backup", "error"}


def test_init_without_yes_under_json_is_a_usage_error_object(tmp_path: Path, capsys) -> None:
    """Review 3, batch 5.9: the UsageError JSON was unpinned."""
    (tmp_path / "AGENTS.md").write_text("rules\n")
    argv = ["init", "project", "--root", str(tmp_path), "--harness", "codex", "--json"]

    assert loadout.main(argv) == 2

    assert _one_object(capsys) == {
        "status": "error",
        "exit_code": 2,
        "error": "init requires --yes after reviewing a --dry-run preview; no changes made",
    }


def test_an_argument_error_in_check_json_prints_the_object(capsys) -> None:
    assert loadout.main(["check", "--json", "--bogus"]) == 2

    document = _one_object(capsys)
    assert (document["status"], document["exit_code"]) == ("error", 2)
    assert "--bogus" in str(document["error"])


def test_an_argument_error_reads_the_process_arguments_when_none_are_passed(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["loadout", "check", "--json", "--bogus"])

    assert loadout.main() == 2

    assert _one_object(capsys)["exit_code"] == 2


def test_an_incomplete_plan_reports_exit_code_2_in_its_envelope(tmp_path: Path, capsys) -> None:
    (tmp_path / "AGENTS.md").write_text("rules\n")

    assert loadout.main(["init", "project", "--root", str(tmp_path), "--json", "--dry-run"]) == 2

    document = _one_object(capsys)
    assert (document["status"], document["exit_code"]) == ("incomplete", 2)
