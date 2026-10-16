"""Global scope renders only from this machine's registered source; a `loadout.toml`
anywhere else is refused before any of it is read or written (ADR 0031)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.written import written_state_path
from registration import register

pytestmark = pytest.mark.integration

REFUSAL = (
    "but this machine has no registered source; register this folder with `loadout init global"
)


def _no_source(root: Path) -> str:
    return (
        f"loadout: {root}: this folder holds a global setup (loadout.toml), but this machine has "
        f"no registered source; register this folder with `loadout init global {root}`\n"
    )


def _elsewhere(root: Path, source: Path) -> str:
    return (
        f"loadout: {root}: this folder holds a global setup (loadout.toml), but this machine's "
        f"registered source is {source}; to make this folder the one, run "
        f"`loadout init global {root} --registration replace`\n"
    )


def _clone(root: Path, destination: str) -> Path:
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("echo pwned\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        f'[instructions.evil]\ndestinations = ["{destination}"]\norder = ["x"]\n'
    )
    return root


def _tree(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


@pytest.mark.parametrize("machine", ["none", "elsewhere"])
def test_a_cloned_global_setup_is_refused_before_it_writes(
    tmp_path: Path, fake_home: Path, capsys, machine: str
) -> None:
    mine = tmp_path / "mine"
    if machine == "elsewhere":
        mine.mkdir()
        register(mine)
    clone = _clone(tmp_path / "clone", "~/.config/fish/conf.d/evil.fish")
    # A record of a vanished root, which a sync that got as far as pruning would delete.
    stale = written_state_path(tmp_path / "vanished")
    stale.parent.mkdir(parents=True)
    stale.write_text(json.dumps({"root": str(tmp_path / "vanished")}))
    before = _tree(fake_home)

    assert loadout.main(["sync", "--root", str(clone)]) == 3

    assert capsys.readouterr().err == (
        _elsewhere(clone, mine) if machine == "elsewhere" else _no_source(clone)
    )
    assert _tree(fake_home) == before


def test_check_of_a_cloned_global_setup_prints_only_the_refusal(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    key = fake_home / ".ssh/id_fake"
    key.parent.mkdir()
    key.write_text("FAKE PRIVATE KEY\n")
    clone = _clone(tmp_path / "clone", "~/.ssh/id_fake")

    assert loadout.main(["check", "--root", str(clone)]) == 3

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", _no_source(clone))


def test_check_json_reports_the_refusal_as_its_one_object(tmp_path: Path, capsys) -> None:
    clone = _clone(tmp_path / "clone", "~/out.md")

    assert loadout.main(["check", "--root", str(clone), "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["exit_code"]) == ("error", 3)
    assert REFUSAL in result["error"]


def test_the_refusal_comes_before_the_manifest_is_parsed(tmp_path: Path, capsys) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "loadout.toml").write_text("this is [not toml\n")

    assert loadout.main(["sync", "--root", str(clone)]) == 3

    assert REFUSAL in capsys.readouterr().err


def _project_with_a_pr_manifest(root: Path) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    return _clone(root, "~/.config/fish/conf.d/evil.fish")


def test_a_project_holding_a_pr_added_global_setup_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    project = _project_with_a_pr_manifest(tmp_path / "p")
    home = _tree(fake_home)
    tree = _tree(project)

    assert loadout.main(["sync", "--root", str(project)]) == 3

    assert REFUSAL in capsys.readouterr().err
    assert (_tree(fake_home), _tree(project)) == (home, tree)


def test_a_staged_global_setup_in_a_project_is_refused(tmp_path: Path, capsys) -> None:
    project = _project_with_a_pr_manifest(tmp_path / "p")
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    for command in (["init", "-q"], ["add", "-A"]):
        subprocess.run(["git", "-C", str(project), *command], check=True, env=environment)
    (project / "loadout.toml").unlink()

    assert loadout.main(["check", "--staged", "--root", str(project)]) == 3

    assert REFUSAL in capsys.readouterr().err


def test_init_project_refuses_a_folder_holding_a_global_setup(tmp_path: Path, capsys) -> None:
    project = _clone(tmp_path / "p", "~/.config/fish/conf.d/evil.fish")
    tree = _tree(project)

    assert loadout.main(["init", "project", "--root", str(project), "--yes"]) == 3

    assert REFUSAL in capsys.readouterr().err
    assert _tree(project) == tree


def _source(root: Path) -> Path:
    return _clone(root, "~/global.md")


@pytest.mark.parametrize("spelling", ["global", "root", "cwd", "link", "relative"])
def test_the_registered_source_syncs_however_it_is_named(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    source = register(_source(tmp_path / "source"))
    (tmp_path / "alias").symlink_to(source, target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    arguments = {
        "global": ["--global"],
        "root": ["--root", str(source)],
        "cwd": [],
        "link": ["--root", str(tmp_path / "alias")],
        "relative": ["--root", "source"],
    }[spelling]
    if spelling == "cwd":
        monkeypatch.chdir(source)

    assert loadout.main(["sync", *arguments]) == 0

    assert (fake_home / "global.md").read_text().endswith("echo pwned\n")


def test_a_relative_registration_registers_no_folder(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    """Review 3, 1.4: `source = "."` made every folder a command ran in the registered one."""
    clone = _clone(tmp_path / "clone", "~/evil.md")
    machine = fake_home / ".config/loadout/config.toml"
    machine.parent.mkdir(parents=True)
    machine.write_text('source = "."\n')
    monkeypatch.chdir(clone)
    before = _tree(fake_home)

    assert loadout.main(["sync"]) == 3

    assert "source '.' must be an absolute path" in capsys.readouterr().err
    assert _tree(fake_home) == before


def _staged_only(project: Path, name: str) -> None:
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["git", "-C", str(project), "init", "-q"], check=True, env=environment)
    (project / "loadout.toml").rename(project / name)
    subprocess.run(["git", "-C", str(project), "add", "-A"], check=True, env=environment)
    (project / name).unlink()


@pytest.mark.parametrize("name", ["Loadout.toml", "LOADOUT.TOML"])
def test_a_case_variant_staged_manifest_is_refused(tmp_path: Path, capsys, name: str) -> None:
    """Review 3, 1.10: on a case-insensitive disk the snapshot reads `Loadout.toml` as the
    manifest, so the staged name is matched case-folded."""
    project = _project_with_a_pr_manifest(tmp_path / "p")
    _staged_only(project, name)

    assert loadout.main(["check", "--staged", "--root", str(project)]) == 3

    assert capsys.readouterr().err == _no_source(project)


@pytest.mark.parametrize(("registered", "code"), [(False, 1), (True, 0)])
def test_the_staged_worker_renders_global_scope_only_when_the_parent_says(
    tmp_path: Path, registered: bool, code: int
) -> None:
    """Review 3, 1.10: the worker takes the registration from the parent rather than
    trusting whatever manifest its snapshot holds."""
    tree = tmp_path / "index"
    _clone(tree, "~/out.md")
    request = {
        "snapshot": str(tree),
        "original": str(tmp_path / "repo"),
        "relative": ".",
        "profile": "default",
        "render": True,
        "environment": {"HOME": str(tmp_path / "home")},
        "gitlinks": [],
        "registered": registered,
    }
    result = subprocess.run(
        [sys.executable, "-I", "-m", "loadout.staged_render"],
        input=json.dumps(request).encode(),
        capture_output=True,
        check=False,
        cwd=tmp_path,
    )

    assert (result.returncode, REFUSAL in result.stderr.decode()) == (code, not registered)


def test_a_broken_machine_config_is_named_rather_than_called_unregistered(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 3, batch 2.6: a typo in the config read as "isn't the registered source"."""
    source = register(_source(tmp_path / "source"))
    machine = fake_home / ".config/loadout/config.toml"
    machine.write_text(machine.read_text() + 'profle = "work"\n')

    assert loadout.main(["sync", "--root", str(source), "--profile", "x"]) == 3

    assert capsys.readouterr().err == f"loadout: {machine}: unknown key(s) profle\n"


def test_a_failed_hook_suggests_the_retry_that_fits(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    clone = _clone(tmp_path / "clone", "~/out.md")

    assert loadout.main(["hook-event", "post-merge", "--root", str(clone)]) == 3

    assert capsys.readouterr().err.endswith(
        "Git merge already completed; loadout regeneration failed. Fix the reported problem, "
        f"then run: loadout sync --root {clone}\n"
    )


def test_a_hook_refused_by_the_guard_repeats_a_profile_it_was_given(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = register(_source(tmp_path / "source"))
    assert loadout.main(["sync", "--root", str(source)]) == 0
    (fake_home / "global.md").write_text("edited by hand\n")
    capsys.readouterr()

    arguments = ["hook-event", "post-merge", "--root", str(source), "--profile", "default"]
    assert loadout.main(arguments) == 1

    assert capsys.readouterr().err.endswith(
        "Git merge already completed; loadout regeneration failed. Preserve output edits, "
        f"reconcile the reported conflict, then run: loadout sync --root {source} --profile default\n"
    )


def test_the_commit_removing_a_pr_added_global_setup_passes(tmp_path: Path, capsys) -> None:
    """Review 3, batch 2.7: HEAD still held the manifest, so the fix itself was refused."""
    project = _project_with_a_pr_manifest(tmp_path / "p")
    environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    git = ["git", "-C", str(project), "-c", "user.email=t@e", "-c", "user.name=t"]
    for command in (
        ["init", "-q"],
        ["add", "-A"],
        ["commit", "-qm", "pr"],
        ["rm", "-q", "loadout.toml"],
    ):
        subprocess.run([*git, *command], check=True, env=environment)
    capsys.readouterr()

    assert loadout.main(["check", "--staged", "--root", str(project)]) == 0

    assert capsys.readouterr().out == "staged loadout dependency snapshot is valid\n"


def test_init_project_from_a_subdirectory_is_refused_at_the_project_root(
    tmp_path: Path, capsys
) -> None:
    """Review 3, batch 2.8: the gate looked at the subdirectory, so the preview ran and
    `--yes` applied before the first sync refused and rolled back."""
    project = _clone(tmp_path / "p", "~/.config/fish/conf.d/evil.fish")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "src").mkdir()
    tree = _tree(project)

    arguments = ["init", "project", "--root", str(project / "src"), "--harness", "claude"]
    assert loadout.main([*arguments, "--dry-run"]) == 3

    assert capsys.readouterr().err == _no_source(project)
    assert _tree(project) == tree


def test_hooks_are_not_installed_for_an_unregistered_global_setup(tmp_path: Path, capsys) -> None:
    """Review 3, batch 2.9: every later commit in the clone was refused instead."""
    clone = _clone(tmp_path / "clone", "~/out.md")
    subprocess.run(["git", "init", "-q", str(clone)], check=True)

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(clone)]) == 3

    assert capsys.readouterr().err == _no_source(clone)
    assert (
        sorted(
            path.name
            for path in (clone / ".git/hooks").iterdir()
            if not path.name.endswith(".sample")
        )
        == []
    )


def test_template_commands_refuse_an_unregistered_dual_scope_root(tmp_path: Path, capsys) -> None:
    project = _project_with_a_pr_manifest(tmp_path / "p")

    assert loadout.main(["template", "list", "--root", str(project)]) == 3

    assert capsys.readouterr().err == _no_source(project)
