"""`check --staged` passes layouts it blocked every commit for (review 4, batch 2.3)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout import machine
from loadout.staged import check_staged
from registration import register

pytestmark = pytest.mark.integration

INSTRUCTIONS = '[instructions.mine]\ndestinations = ["~/out.md"]\norder = ["x"]\n'


def _git(root: Path, *args: str) -> None:
    # Bounded, so a git that hangs fails the test rather than the run (review 5, 5.9).
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=120)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)


def _staged(root: Path) -> Path:
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return root


def _passes(root: Path, capsys) -> None:
    capsys.readouterr()
    assert loadout.main(["check", "--staged", "--root", str(root)]) == 0, capsys.readouterr().err
    assert capsys.readouterr().out == "staged loadout dependency snapshot is valid\n"


def test_an_artifact_folder_linked_inside_the_repository(tmp_path: Path, capsys) -> None:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "."\n\n{INSTRUCTIONS}',
            "vendor/instr/x.md": "Rules.\n",
        },
    )
    (root / "instructions").symlink_to("vendor/instr")
    register(_staged(root))

    _passes(root, capsys)


@pytest.mark.parametrize(
    ("path", "link", "target"), [("src", "src", "real/src"), ("lib/src", "lib", "real")]
)
def test_a_source_path_through_a_link_inside_the_repository(
    tmp_path: Path, capsys, path: str, link: str, target: str
) -> None:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "{path}"\n\n{INSTRUCTIONS}',
            f"{target}/{Path(path).relative_to(link)}/instructions/x.md": "Rules.\n",
        },
    )
    (root / link).parent.mkdir(parents=True, exist_ok=True)
    (root / link).symlink_to(os.path.relpath(root / target, (root / link).parent))
    register(_staged(root))

    _passes(root, capsys)


def test_a_linked_folder_reached_through_another_link(tmp_path: Path, capsys) -> None:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "."\n\n{INSTRUCTIONS}',
            "third/vendor/instr/x.md": "Rules.\n",
        },
    )
    (root / "vendor").symlink_to("third/vendor")
    (root / "instructions").symlink_to("vendor/instr")
    register(_staged(root))

    _passes(root, capsys)


def test_a_source_file_that_is_a_link_inside_the_repository(tmp_path: Path, capsys) -> None:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\n\n[claude]\n',
            "shared/p.toml": '[shell]\nallow = ["ls"]\n',
        },
    )
    (root / "permissions.toml").symlink_to("shared/p.toml")
    register(_staged(root))

    _passes(root, capsys)


def test_a_dual_scope_root_with_a_global_source_outside_its_project_folder(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "global"\n\n{INSTRUCTIONS}',
            "global/instructions/x.md": "Rules.\n",
            "loadout/config.toml": 'harnesses = ["codex"]\n',
        },
    )
    register(_staged(root))

    _passes(root, capsys)


def test_a_manifest_on_disk_but_not_in_the_index(tmp_path: Path, capsys) -> None:
    """`git rm --cached loadout.toml` is how a project drops a manifest it should not have
    committed, and the registration gate read the disk copy and refused it anyway."""
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\n',
            "loadout/config.toml": 'harnesses = ["codex"]\n',
        },
    )
    _staged(root)
    _git(root, "rm", "-q", "--cached", "loadout.toml")

    _passes(root, capsys)


def test_a_permission_base_outside_every_source_folder(root: Path, capsys) -> None:
    """Review 4, batch 2.4: `base` files were left out of the snapshot."""
    register(_staged(root))
    # The fixture seeds an output a co-owner wrote, which is no input.
    _git(root, "rm", "-rq", "--cached", "perm")

    _passes(root, capsys)


def test_check_staged_reads_the_machine_config_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 4, batch 2.10: three reads, and the refusal's message a fourth, so the
    decision and what it said could disagree."""
    root = tmp_path / "repo"
    _write(
        root,
        {
            "loadout.toml": f'[[source]]\nname = "s"\npath = "."\n\n{INSTRUCTIONS}',
            "instructions/x.md": "Rules.\n",
        },
    )
    register(_staged(root))
    reads: list[Path] = []
    real = machine.load_machine_config

    def counting(path: Path, **kwargs: bool) -> object:
        reads.append(path)
        return real(path, **kwargs)

    monkeypatch.setattr(machine, "load_machine_config", counting)

    assert check_staged(root) == 0
    assert len(reads) == 1


def test_a_link_resolving_through_itself_ends_rather_than_looping(tmp_path: Path) -> None:
    """Review 5, batch 2.5: `loadout/lp -> lp/b` with a source at `lp/src` looped forever.
    In a child, so a regression fails rather than hangs."""
    repo = tmp_path / "repo"
    (repo / "loadout").mkdir(parents=True)
    (repo / "loadout/loadout.toml").write_text('[[source]]\nname = "s"\npath = "lp/src"\n')
    (repo / "loadout/lp").symlink_to("lp/b")
    _staged(repo)
    register(repo / "loadout")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "loadout", "check", "--staged", "--root", str(repo / "loadout")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("check --staged did not finish within 60 s")

    assert result.returncode == 3, result.stderr


def test_a_staged_manifest_in_another_case_is_advised_out_of_the_index(
    tmp_path: Path, capsys
) -> None:
    """Review 5, batch 4.1: the refusal advised registering the project as a global source."""
    root = tmp_path / "repo"
    _write(
        root,
        {
            "Loadout.toml": '[[source]]\nname = "s"\npath = "."\n',
            "loadout/config.toml": 'harnesses = ["codex"]\n',
        },
    )
    _staged(root)
    capsys.readouterr()

    assert loadout.main(["check", "--staged", "--root", str(root)]) == 3

    err = capsys.readouterr().err
    assert "unstage it with `git rm --cached -- Loadout.toml`" in err
    assert "--registration" not in err
