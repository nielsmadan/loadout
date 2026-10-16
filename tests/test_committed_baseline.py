"""The committed baseline holds what the render reads, and only that (review 4, batch 2.4)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout
from loadout import sync
from loadout.emit import RenderContext
from loadout.written import written_state_path
from registration import register

pytestmark = pytest.mark.integration

INSTRUCTIONS = '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)


def _committed(root: Path) -> Path:
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def test_a_dual_scope_root_s_global_output_matching_the_commit_is_accepted(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Only `loadout/` was read from the commit, so with no record a global output the commit
    accounts for was refused as `unknown`."""
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "global"\n\n{INSTRUCTIONS}',
            "global/instructions/x.md": "Rules.\n",
            "loadout/config.toml": 'harnesses = ["codex"]\n',
        },
    )
    register(_committed(root))
    assert loadout.main(["sync", "--root", str(root)]) == 0
    written_state_path(root).unlink()
    (root / "global/instructions/x.md").write_text("Changed.\n")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (fake_home / "out.md").read_text().endswith("Changed.\n")


def test_a_global_source_s_baseline_holds_only_what_its_render_reads(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole repository was written out: 20 s on 64k files for one refusal."""
    root = tmp_path / "repo"
    _write(
        root,
        {
            "config/loadout.toml": f'[[source]]\nname = "s"\npath = "."\n\n{INSTRUCTIONS}',
            "config/instructions/x.md": "Rules.\n",
            "src/app.py": "print()\n",
            "assets/big.bin": "payload\n",
        },
    )
    source = register(_committed(root) / "config")
    written: list[str] = []
    real = sync.write_snapshot

    def recording(repository: Path, destination: Path, entries: dict) -> None:
        written.extend(entries)
        real(repository, destination, entries)

    monkeypatch.setattr(sync, "write_snapshot", recording)

    variants = sync._committed_variants(sync.sync_state(source), ["default"], RenderContext())

    assert sorted(written) == ["config/instructions/x.md", "config/loadout.toml"]
    assert any(path.name == "out.md" for path in variants)
