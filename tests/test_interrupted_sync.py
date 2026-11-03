"""A sync stopped with Ctrl-C at any point is completed by the next sync.

Sync is not transactional: what it wrote before the interrupt stays. So the record must name
only what was written, nothing may be left half-retired or reported as hand-edited, and no
write in progress may leave its temporary file behind.
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
from loadout import atomic, files, retirement
from loadout.written import read_written

pytestmark = pytest.mark.integration

STOPPED = "loadout: sync stopped with Ctrl-C; run it again to finish\n"


def _interrupt_on(call: int, original: Callable[..., Any]) -> Callable[..., Any]:
    """`original`, with SIGINT sent to this process, as a terminal sends it, at its `call`th
    use: before it runs, or, for `call` < 0, after."""
    calls = 0

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == call:
            os.kill(os.getpid(), signal.SIGINT)
        result = original(*args, **kwargs)
        if calls == -call:
            os.kill(os.getpid(), signal.SIGINT)
        return result

    return interrupted


def _skill(root: Path, name: str) -> None:
    folder = root / "loadout/skills" / name
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nx\n")
    (folder / "ref.md").write_text("ref\n")


@pytest.fixture
def project(tmp_path: Path, capsys) -> Path:
    root = tmp_path / "p"
    root.mkdir()
    (root / "CLAUDE.md").write_text("# one\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"]) == 0
    )
    _skill(root, "old")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    capsys.readouterr()
    return root


def _temporaries(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if atomic.temporary_name(path.name)]


def _finishes(root: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert loadout.main(["check", "--root", str(root)]) == 0
    capsys.readouterr()


def test_an_interrupt_while_writing_is_finished_by_the_next_sync(
    project: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "loadout/instructions/main.md").write_text("# two\n")
    _skill(project, "new")
    record = read_written(project)
    original = files._write_output
    monkeypatch.setattr(files, "_write_output", _interrupt_on(-2, original))

    assert loadout.main(["sync", "--root", str(project)]) == 130
    assert capsys.readouterr().err == STOPPED
    monkeypatch.setattr(files, "_write_output", original)

    assert read_written(project) == record
    assert _temporaries(project) == []
    assert loadout.main(["check", "--root", str(project), "--json"]) == 1
    drift = json.loads(capsys.readouterr().out)["drift"]
    assert sorted({item["kind"] for item in drift}) == ["source-changed"]
    _finishes(project, capsys)
    assert (project / ".claude/skills/new/ref.md").read_text() == "ref\n"


def test_an_interrupt_while_retiring_is_finished_by_the_next_sync(
    project: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("SKILL.md", "ref.md"):
        (project / "loadout/skills/old" / name).unlink()
    (project / "loadout/skills/old").rmdir()
    original = retirement._prune_skill_folder
    monkeypatch.setattr(retirement, "_prune_skill_folder", _interrupt_on(2, original))

    assert loadout.main(["sync", "--root", str(project)]) == 130
    assert capsys.readouterr().err == STOPPED
    monkeypatch.setattr(retirement, "_prune_skill_folder", original)

    assert sorted(p.name for p in (project / ".claude/skills").iterdir()) == ["old"]
    _finishes(project, capsys)
    assert list((project / ".claude/skills").iterdir()) == []


def test_an_interrupt_as_a_write_begins_leaves_no_temporary_file(
    project: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "loadout/instructions/main.md").write_text("# two\n")
    original = atomic.tempfile.mkstemp
    monkeypatch.setattr(atomic.tempfile, "mkstemp", _interrupt_on(-1, original))

    assert loadout.main(["sync", "--root", str(project)]) == 130
    monkeypatch.setattr(atomic.tempfile, "mkstemp", original)

    assert _temporaries(project) == []
    _finishes(project, capsys)
