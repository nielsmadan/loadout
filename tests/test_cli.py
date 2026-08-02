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
    for src in (GOLDEN / "global" / "fragments").glob("*.md"):
        (fragments / src.name).write_text(src.read_text())
    (tmp_path / "loadout.toml").write_text(
        (GOLDEN / "manifest.toml").read_text(encoding="utf-8"), encoding="utf-8"
    )
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


def test_missing_manifest_returns_3(tmp_path: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 3
    assert "manifest" in capsys.readouterr().err


def test_missing_fragment_file_returns_3(root: Path, capsys) -> None:
    (root / "global" / "fragments" / "secrets.md").unlink()
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "secrets" in capsys.readouterr().err


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


def test_explain_reports_the_source_and_path(root: Path, capsys) -> None:
    assert loadout.main(["explain", "web-fetching", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "web-fetching" in out
    assert "ac" in out


def test_explain_lists_targets_that_use_the_fragment(root: Path, capsys) -> None:
    assert loadout.main(["explain", "git-policy", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "claude/CLAUDE.md" in out
    assert "claude/CLAUDE.autonomous.md" not in out


def test_explain_survives_an_unrelated_unresolvable_fragment(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"working-style"', '"no-such-fragment"', 1),
        encoding="utf-8",
    )
    assert loadout.main(["explain", "git-policy", "--root", str(root)]) == 0
    captured = capsys.readouterr()
    assert "used by:" in captured.out
    assert "no-such-fragment" in captured.err


def test_explain_finds_users_when_queried_by_qualified_name(root: Path, capsys) -> None:
    assert loadout.main(["explain", "ac/git-policy", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "claude/CLAUDE.md" in out
    assert "no target lists it" not in out


def test_explain_on_unknown_name_returns_3(root: Path, capsys) -> None:
    assert loadout.main(["explain", "nope", "--root", str(root)]) == 3
    assert "nope" in capsys.readouterr().err


def test_sync_with_unknown_fragment_returns_3(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"intro-claude"', '"no-such-fragment"'),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "no-such-fragment" in capsys.readouterr().err


def test_sync_with_ambiguous_fragment_returns_3(root: Path, capsys) -> None:
    second = root / "second" / "global" / "fragments"
    second.mkdir(parents=True)
    (second / "web-fetching.md").write_text("duplicate\n", encoding="utf-8")
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '[[source]]\nname = "ac"\npath = "."',
            '[[source]]\nname = "ac"\npath = "."\n\n[[source]]\nname = "second"\npath = "second"',
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    err = capsys.readouterr().err
    assert "ac/web-fetching" in err
    assert "second/web-fetching" in err


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
