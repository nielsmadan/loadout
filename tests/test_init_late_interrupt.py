"""Ctrl-C reports what init actually did, by the phase it arrives in.

A Ctrl-C just after apply finished said "nothing was changed" over a completed conversion and
left its backup behind; one during apply said "init failed (KeyboardInterrupt)".
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import loadout
from loadout import init_workflow

pytestmark = pytest.mark.migration_integration

LATE = "Ctrl-C arrived after the conversion had finished, so it was completed."


def _interrupting(original: Callable[..., Any]) -> Callable[..., Any]:
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        os.kill(os.getpid(), signal.SIGINT)
        return original(*args, **kwargs)

    return interrupted


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# P\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _argv(root: Path, *extra: str) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "claude", "--yes", *extra]


def _backups(home: Path) -> list[Path]:
    folder = home / ".local/state/loadout/backups"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def test_ctrl_c_while_init_cleans_up_completes_it(
    repository: Path, fake_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_workflow, "discard", _interrupting(init_workflow.discard))

    assert loadout.main(_argv(repository)) == 0
    out = capsys.readouterr().out
    assert "Migration complete" in out
    assert out.splitlines()[-1] == LATE
    assert _backups(fake_home) == []
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert loadout.main(["check", "--root", str(repository)]) == 0


def test_ctrl_c_while_init_cleans_up_prints_the_normal_envelope(
    repository: Path, fake_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_workflow, "discard", _interrupting(init_workflow.discard))

    assert loadout.main(_argv(repository, "--json")) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert (envelope["status"], envelope["exit_code"]) == ("complete", 0)
    assert _backups(fake_home) == []


def test_ctrl_c_during_apply_says_it_was_put_back(
    repository: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        init_workflow, "apply_migration", _interrupting(init_workflow.apply_migration)
    )

    assert loadout.main(_argv(repository)) == 130
    assert capsys.readouterr().err.splitlines()[0] == (
        "loadout: init stopped with Ctrl-C; everything it changed was put back"
    )
    assert sorted(p.name for p in repository.iterdir()) == [".git", "CLAUDE.md"]


def test_ctrl_c_while_init_backs_up_has_changed_nothing(
    repository: Path, fake_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_workflow, "create_backup", _interrupting(init_workflow.create_backup))

    assert loadout.main(_argv(repository)) == 130
    assert capsys.readouterr().err == "loadout: init stopped with Ctrl-C; nothing was changed\n"
    assert _backups(fake_home) == []
