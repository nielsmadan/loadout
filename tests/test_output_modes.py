"""A generated text output's mode (review 4, batch 1.4): every one landed 0600, the
temporary file's mode, and a rewrite reverted the user's chmod."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

import loadout
from loadout import files
from loadout.files import atomic_write, process_umask, text_mode, write_outputs
from registration import register

TOKEN = "ghp_" + "a1B2" * 9


@pytest.fixture
def umask_022() -> Iterator[None]:
    previous = os.umask(0o022)
    yield
    os.umask(previous)


@pytest.mark.parametrize(("umask", "mode"), [(0o022, 0o644), (0o002, 0o664), (0o077, 0o600)])
def test_a_new_file_gets_what_the_umask_allows(umask: int, mode: int) -> None:
    assert text_mode(None, b"Rules.\n", "md", umask) == mode


def test_a_new_file_holding_a_secret_is_owner_only() -> None:
    assert text_mode(None, f"push with {TOKEN}\n".encode(), "md", 0o022) == 0o600


def test_a_replaced_file_keeps_its_mode_whatever_it_holds() -> None:
    assert text_mode(0o640, f"push with {TOKEN}\n".encode(), "md", 0o022) == 0o640


def test_a_new_text_output_is_written_as_the_umask_allows(tmp_path: Path, umask_022: None) -> None:
    path = tmp_path / "AGENTS.md"

    atomic_write(path, "Rules.\n")

    assert path.stat().st_mode & 0o777 == 0o644


def test_a_rewrite_keeps_the_user_s_chmod(tmp_path: Path, umask_022: None) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("Old.\n")
    path.chmod(0o640)

    atomic_write(path, "New.\n")

    assert (path.read_text(), path.stat().st_mode & 0o777) == ("New.\n", 0o640)


def test_a_rewrite_through_a_link_keeps_its_target_s_mode(tmp_path: Path, umask_022: None) -> None:
    real = tmp_path / "repo/AGENTS.md"
    real.parent.mkdir()
    real.write_text("Old.\n")
    real.chmod(0o604)
    link = tmp_path / "AGENTS.md"
    link.symlink_to(real)

    atomic_write(link, "New.\n")

    assert (real.read_text(), real.stat().st_mode & 0o777) == ("New.\n", 0o604)


@pytest.mark.integration
def test_sync_writes_outputs_readable_and_keeps_a_chmod(
    tmp_path: Path, fake_home: Path, umask_022: None
) -> None:
    root = register(tmp_path / "source")
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("Rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'
    )
    assert loadout.main(["sync", "--root", str(root)]) == 0
    output = fake_home / "out.md"
    created = output.stat().st_mode & 0o777
    output.chmod(0o640)
    (root / "instructions/x.md").write_text("Changed.\n")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (created, output.stat().st_mode & 0o777) == (0o644, 0o640)
    assert output.read_text().endswith("Changed.\n")


def test_writing_many_outputs_reads_the_umask_once(
    tmp_path: Path, umask_022: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5, batch 3.3: reading the umask sets it, and was done for every file."""
    reads: list[int] = []
    real = files.process_umask

    def counting() -> int:
        reads.append(1)
        return real()

    monkeypatch.setattr(files, "process_umask", counting)

    write_outputs({tmp_path / f"out{index}.md": f"{index}\n" for index in range(5)})

    assert (len(reads), (tmp_path / "out3.md").stat().st_mode & 0o777) == (1, 0o644)


def test_a_new_document_holding_only_a_structural_secret_is_owner_only(tmp_path: Path) -> None:
    """Review 6, batch 3 (tm-format): the credential check is told the document's
    format, so an `env` entry only its structure flags counts."""
    target = tmp_path / "new.json"

    atomic_write(target, '{"env": {"MY_SERVICE": "FAKEabcdef123456"}}\n', umask=0o022)

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_reading_the_umask_leaves_it_as_it_was() -> None:
    """Review 6, batch 3 (tm-restore-umask): reading sets it, so it is set back."""
    before = os.umask(0o027)
    try:
        read = process_umask()
        after = os.umask(0o027)
    finally:
        os.umask(before)

    assert (read, after) == (0o027, 0o027)
