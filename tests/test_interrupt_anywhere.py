"""Ctrl-C at any point exits 130 without a traceback, and under `--json` prints only the envelope.

Init's apply already did; a SIGINT during its planning, or during any other command, escaped as
a `KeyboardInterrupt` traceback and death by signal, with no envelope for a JSON consumer.
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
from loadout import commands, init_workflow

pytestmark = pytest.mark.integration


def _interrupting(original: Callable[..., Any]) -> Callable[..., Any]:
    """The function, once SIGINT has reached this process the way a terminal sends it."""

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


def test_ctrl_c_while_init_plans_exits_130(
    repository: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_workflow, "discover", _interrupting(init_workflow.discover))

    argv = ["init", "project", "--root", str(repository), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 130
    assert capsys.readouterr().err == "loadout: init stopped with Ctrl-C; nothing was changed\n"
    assert sorted(p.name for p in repository.iterdir()) == [".git", "CLAUDE.md"]


def test_ctrl_c_while_init_plans_prints_only_the_envelope(
    repository: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(init_workflow, "discover", _interrupting(init_workflow.discover))

    argv = ["init", "project", "--root", str(repository), "--harness", "claude", "--json"]
    assert loadout.main(argv) == 130
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "exit_code": 130,
        "error": "init stopped with Ctrl-C; nothing was changed",
    }


@pytest.fixture
def project(repository: Path, capsys) -> Path:
    argv = ["init", "project", "--root", str(repository), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0
    (repository / "loadout/instructions/main.md").write_text("# two\n")
    capsys.readouterr()
    return repository


def test_ctrl_c_while_sync_renders_exits_130(
    project: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "render_all", _interrupting(commands.render_all))

    assert loadout.main(["sync", "--root", str(project)]) == 130
    assert capsys.readouterr().err == "loadout: sync stopped with Ctrl-C; run it again to finish\n"


def test_ctrl_c_while_check_renders_prints_only_the_envelope(
    project: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands, "classified_drift", _interrupting(commands.classified_drift))
    monkeypatch.setattr(commands, "_check_json", _interrupting(commands._check_json))

    assert loadout.main(["check", "--root", str(project), "--json"]) == 130
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "exit_code": 130,
        "error": "stopped with Ctrl-C",
    }
