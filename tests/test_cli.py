from __future__ import annotations

from pathlib import Path

import pytest

import loadout


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
