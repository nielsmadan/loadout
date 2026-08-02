from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.errors import LoadoutError


def test_version_is_exposed() -> None:
    assert isinstance(loadout.__version__, str)
    assert loadout.__version__


def test_no_args_prints_usage_and_returns_2(capsys) -> None:
    assert loadout.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    fragments = tmp_path / "global" / "fragments"
    fragments.mkdir(parents=True)
    for src in (GOLDEN / "fragments").glob("*.md"):
        (fragments / src.name).write_text(src.read_text())
    return tmp_path


def test_sync_writes_files_and_returns_0(root: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert (root / "global" / "AGENTS.md").is_file()
    assert "AGENTS.md" in capsys.readouterr().out


def test_check_returns_0_when_clean(root: Path) -> None:
    loadout.main(["sync", "--root", str(root)])
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_check_returns_1_and_diffs_on_drift(root: Path, capsys) -> None:
    loadout.main(["sync", "--root", str(root)])
    (root / "global" / "AGENTS.md").write_text("tampered\n")
    assert loadout.main(["check", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "tampered" in err


def test_missing_fragments_dir_returns_3(tmp_path: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 3
    assert "fragments" in capsys.readouterr().err


def test_missing_fragment_file_returns_3(root: Path, capsys) -> None:
    (root / "global" / "fragments" / "secrets.md").unlink()
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "secrets.md" in capsys.readouterr().err


def test_unexpected_exception_returns_4_with_traceback(root: Path, monkeypatch, capsys) -> None:
    def boom(_root: Path) -> int:
        raise ValueError("kaboom")

    monkeypatch.setattr(loadout.cli, "cmd_sync", boom)
    assert loadout.main(["sync", "--root", str(root)]) == 4
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "ValueError: kaboom" in err


def test_loadout_error_still_returns_3(root: Path, monkeypatch, capsys) -> None:
    def fail(_root: Path) -> int:
        raise LoadoutError("deliberate failure")

    monkeypatch.setattr(loadout.cli, "cmd_sync", fail)
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "deliberate failure" in capsys.readouterr().err


def test_sync_succeeds_under_a_non_utf8_locale(root: Path, monkeypatch) -> None:
    # Fragments contain non-ASCII characters (em-dash, ellipsis). Under a
    # locale whose default encoding is ASCII, file I/O must still work
    # because loadout always opens files as UTF-8 explicitly.
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("PYTHONCOERCECLOCALE", "0")
    monkeypatch.setenv("PYTHONUTF8", "0")
    result = subprocess.run(
        [sys.executable, "-m", "loadout", "sync", "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "global" / "AGENTS.md").read_text(encoding="utf-8")
