"""The one atomic writer every file loadout writes goes through (review 3, batch 4.4)."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import pytest

from loadout import output
from loadout.errors import LoadoutError
from loadout.output import FrozenFile, atomic_install, replace_atomically


def test_a_write_that_fails_leaves_the_target_and_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text("old\n")

    def fail(handle: BinaryIO) -> None:
        handle.write(b"half")
        raise RuntimeError("killed")

    with pytest.raises(RuntimeError):
        replace_atomically(target, fail)

    assert (target.read_text(), sorted(p.name for p in tmp_path.iterdir())) == (
        "old\n",
        ["doc.json"],
    )


def test_an_install_checks_for_a_link_again_just_before_the_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A link put in the way while the file was being written is still refused."""
    target = tmp_path / "hook"
    target.write_text("old\n")
    calls: list[Path] = []

    def refuse_second(path: Path, root: Path | None = None) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise LoadoutError(f"path is a symlink: {path}")

    monkeypatch.setattr(output, "no_symlinks", refuse_second)

    with pytest.raises(LoadoutError, match="path is a symlink"):
        atomic_install(target, FrozenFile(b"new\n", 0o755), tmp_path)

    assert (target.read_text(), len(calls)) == ("old\n", 2)
