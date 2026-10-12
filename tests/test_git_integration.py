from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.emit import check_all, render_all, write_all
from loadout.errors import LoadoutError
from loadout.git_hooks import install_hooks, plan_hooks, uninstall_hooks
from loadout.machine import machine_config_path
from loadout.staged import check_staged
from test_codex_defaults import build

pytestmark = pytest.mark.migration_integration


def _git(root, *args, check=True, env=None):
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "Fixture")
    _git(tmp_path, "config", "user.email", "fixture@example.invalid")
    return tmp_path


def _project(root):
    source = root / "loadout"
    source.mkdir(parents=True)
    (source / "config.toml").write_text(
        'harnesses=["codex"]\npresets=false\nartifacts="artifacts.toml"\n'
    )
    (source / "artifacts.toml").write_text(
        '[[artifact]]\nagents=["codex"]\nformat="copy"\ncategory="instructions"\n'
        'output="AGENTS.md"\nsource="instructions.md"\n'
    )
    (source / "instructions.md").write_text("original\n")
    return source


def _commit(repo, *paths):
    _git(repo, "add", "--", *paths)
    return _git(repo, "commit", "-qm", "fixture")


def test_staged_ownership_record_matches_its_producer_and_allows_updates(repo):
    build(repo, {"model": "fixture"})
    write_all(repo)
    _commit(repo, "loadout.toml", "permissions.toml", "defaults")
    record = repo / "defaults/codex.owned"
    record.write_text(record.read_text() + "sandbox_mode\n")
    _git(repo, "add", "defaults/codex.owned")
    with pytest.raises(LoadoutError, match="ownership record"):
        check_staged(repo)
    (repo / "defaults/codex.json").write_text('{"model":"fixture","reasoning_effort":"high"}')
    write_all(repo)
    _git(repo, "add", "defaults")
    assert check_staged(repo) == 0
    _commit(repo, "defaults")
    (repo / "defaults/codex.json").write_text('{"model":"fixture"}')
    write_all(repo)
    _git(repo, "add", "defaults")
    assert check_staged(repo) == 0


def test_staged_ownership_record_deletion_requires_producer_retirement(repo):
    build(repo, {"model": "fixture"})
    write_all(repo)
    _commit(repo, "loadout.toml", "permissions.toml", "defaults")
    record = repo / "defaults/codex.owned"
    expected = record.read_text()
    assert check_staged(repo) == 0
    _git(repo, "rm", "defaults/codex.owned")
    assert [path for path, _, _ in check_all(repo)] == [record]
    record.write_text(expected)
    with pytest.raises(LoadoutError, match="ownership record"):
        check_staged(repo)
    _git(repo, "add", "defaults/codex.owned")
    assert check_staged(repo) == 0
    _git(repo, "rm", "defaults/codex.owned")
    manifest = repo / "loadout.toml"
    manifest.write_text(manifest.read_text().replace('defaults = "codex"', "defaults = false"))
    _git(repo, "add", "loadout.toml")
    assert check_staged(repo) == 0


@pytest.mark.parametrize("fragment", [{"model": "fixture"}, {}])
def test_staged_initial_producer_requires_its_generated_record(repo, fragment):
    build(repo, fragment)
    _git(repo, "add", "loadout.toml", "permissions.toml", "defaults")
    with pytest.raises(LoadoutError, match="ownership record"):
        check_staged(repo)
    write_all(repo)
    _git(repo, "add", "defaults/codex.owned")
    assert check_staged(repo) == 0


@pytest.mark.parametrize("name", ["frontend", "private-template"])
def test_bare_templates_never_fall_back_to_machine_or_bundled_source(repo, fake_home, name):
    source = _project(repo)
    (source / "config.toml").write_text(f'harnesses=["codex"]\ntemplates=["{name}"]\n')
    (source / "permissions.toml").write_text("")
    external = fake_home / "private-source"
    template = external / "templates" / name
    template.mkdir(parents=True)
    (template / "instructions.md").write_text("machine override\n")
    (external / "loadout.toml").write_text(
        '[[source]]\nname="private"\npath="."\n[claude]\npermissions=false\n'
    )
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text(f'source="{external}"\n')
    _git(repo, "add", "loadout")
    with pytest.raises(LoadoutError, match=f"template '{name}' is not staged") as error:
        check_staged(repo)
    assert f"loadout template add {name} --vendored" in str(error.value)


def test_nested_global_snapshot_contains_sibling_sources_and_isolates_destinations(repo, fake_home):
    root = repo / "nested/global"
    root.mkdir(parents=True)
    shared = repo / "nested/shared/instructions"
    shared.mkdir(parents=True)
    (shared / "rule.md").write_text("staged instructions\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname="shared"\npath="../shared"\n'
        '[instructions.codex]\noutput="generated.md"\norder=["rule"]\n'
        'destinations=["~/.codex/AGENTS.md"]\n'
    )
    _git(repo, "add", "nested")
    assert check_staged(root) == 0
    assert loadout.main(["check", "--staged", "--root", str(root)]) == 0
    assert list(fake_home.iterdir()) == []
    _git(repo, "rm", "--cached", "nested/shared/instructions/rule.md")
    with pytest.raises(LoadoutError, match="snapshot is incomplete or invalid"):
        check_staged(root)


@pytest.mark.parametrize("old_source", ["absolute", "escaping-symlink"])
def test_head_source_migration_keeps_head_and_index_output_ownership(repo, fake_home, old_source):
    instructions = repo / "instructions"
    instructions.mkdir()
    (instructions / "rule.md").write_text("source instructions\n")
    source_path = str(repo)
    if old_source == "escaping-symlink":
        external = fake_home / "legacy"
        (external / "instructions").mkdir(parents=True)
        (external / "instructions/rule.md").write_text("source instructions\n")
        (repo / "legacy").symlink_to(external, target_is_directory=True)
        source_path = "legacy"
    manifest = repo / "loadout.toml"
    manifest.write_text(
        f'[[source]]\nname="local"\npath="{source_path}"\n'
        '[instructions.codex]\noutput="default.md"\norder=["rule"]\n'
    )
    output = repo / "default.md"
    output.write_text(render_all(repo)[output])
    _commit(
        repo,
        "loadout.toml",
        "instructions",
        "default.md",
        *(["legacy"] if old_source == "escaping-symlink" else []),
    )
    manifest.write_text(
        manifest.read_text()
        .replace(f'path="{source_path}"', 'path="."')
        .replace('output="default.md"', 'output="next.md"')
    )
    _git(repo, "add", "loadout.toml")
    if old_source == "escaping-symlink":
        _git(repo, "rm", "legacy")
        (external / "instructions/rule.md").unlink()
        (external / "instructions").rmdir()
        external.rmdir()
    rendered = render_all(repo)
    assert set(rendered) == {repo / "next.md"}
    assert "source instructions" in rendered[repo / "next.md"]
    assert check_staged(repo) == 0
    output.write_text("edited old output\n")
    _git(repo, "add", "default.md")
    with pytest.raises(
        LoadoutError, match="generated files are staged for addition or editing"
    ) as error:
        check_staged(repo)
    assert "default.md" in str(error.value)
    _git(repo, "rm", "--cached", "default.md")
    assert check_staged(repo) == 0
    (repo / "next.md").write_text("edited new output\n")
    _git(repo, "add", "next.md")
    with pytest.raises(
        LoadoutError, match="generated files are staged for addition or editing"
    ) as error:
        check_staged(repo)
    assert "next.md" in str(error.value)


@pytest.mark.parametrize("target", ["instructions", "permissions"])
def test_staged_ownership_selects_profile_before_resolving_destinations(repo, monkeypatch, target):
    monkeypatch.delenv("LOADOUT_REVIEW_OTHER", raising=False)
    (repo / "instructions").mkdir()
    (repo / "instructions/rule.md").write_text("selected instructions\n")
    (repo / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n')
    other_input = 'order=["rule"]' if target == "instructions" else 'render="codex"'
    (repo / "loadout.toml").write_text(
        '[[source]]\nname="local"\npath="."\n'
        '[instructions.default]\noutput="default.md"\norder=["rule"]\n'
        f'[{target}.other]\nprofile="other"\n{other_input}\noutput="other.md"\n'
        'destinations=["${LOADOUT_REVIEW_OTHER}/AGENTS.md"]\n'
    )
    _commit(repo, "loadout.toml", "instructions", "permissions.toml")
    assert set(render_all(repo)) == {repo / "default.md"}
    (repo / "other.md").write_text("authored under default profile\n")
    _git(repo, "add", "other.md")
    assert check_staged(repo) == 0
    with pytest.raises(LoadoutError, match=r"LOADOUT_REVIEW_OTHER.*unset"):
        check_staged(repo, "other")
    monkeypatch.setenv("LOADOUT_REVIEW_OTHER", str(repo / "other-destination"))
    assert set(render_all(repo, "other")) == {
        repo / "default.md",
        repo / "other.md",
        repo / "other-destination/AGENTS.md",
    }
    with pytest.raises(
        LoadoutError, match="generated files are staged for addition or editing"
    ) as error:
        check_staged(repo, "other")
    assert "other.md" in str(error.value)


def test_hook_installation_preserves_occupied_shared_and_other_source_hooks(repo, tmp_path_factory):
    _project(repo)
    _commit(repo, "loadout")
    custom = repo / "custom-hooks"
    _git(repo, "config", "core.hooksPath", "custom-hooks")
    install_hooks(repo, dry_run=True)
    assert not custom.exists()
    install_hooks(repo)
    hook = custom / "pre-commit"
    before = hook.read_bytes()
    assert plan_hooks(repo).hooks[0].status == "managed"
    nested = repo / "nested"
    _project(nested)
    assert plan_hooks(nested).hooks[0].status == "occupied"
    install_hooks(nested)
    assert hook.read_bytes() == before
    hook.write_text("#!/bin/sh\nexit 0\n")
    install_hooks(repo)
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"
    shared = tmp_path_factory.mktemp("shared-hooks")
    _git(repo, "config", "core.hooksPath", str(shared))
    assert plan_hooks(repo).hooks[0].status == "external"
    install_hooks(repo)
    assert list(shared.iterdir()) == []


@pytest.mark.parametrize("custom", [False, True])
def test_main_checkout_preserves_hooks_shared_with_linked_worktrees(
    repo, tmp_path_factory, capsys, custom
):
    _project(repo)
    _commit(repo, "loadout")
    directory = repo / ("custom-hooks" if custom else ".git/hooks")
    if custom:
        _git(repo, "config", "core.hooksPath", str(directory))
    linked = tmp_path_factory.mktemp("shared-main-hooks") / "linked"
    _git(repo, "worktree", "add", "--no-checkout", "-qb", "linked", str(linked))
    main_plan, linked_plan = plan_hooks(repo), plan_hooks(linked)
    assert main_plan.directory == linked_plan.directory == directory
    assert main_plan.hooks[0].status == "shared"
    assert linked_plan.hooks[0].status == "shared"
    install_hooks(repo)
    diagnostic = capsys.readouterr().out
    assert main_plan.hooks[0].command in diagnostic
    assert "repo=$(git rev-parse --show-toplevel)" in diagnostic
    assert not (directory / "pre-commit").exists()
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(linked, "config", "--worktree", "core.hooksPath", ".linked-hooks")
    assert plan_hooks(repo).hooks[0].status == "install"
    install_hooks(repo)
    assert plan_hooks(repo).hooks[0].status == "managed"
    assert (directory / "pre-commit").read_bytes() == main_plan.hooks[0].content


def test_standalone_install_rechecks_hook_sharing_after_preview(
    repo, tmp_path_factory, monkeypatch
):
    _project(repo)
    _commit(repo, "loadout")
    linked = tmp_path_factory.mktemp("late-sharing") / "linked"

    def share(preview):
        assert preview["hooks"][0]["status"] == "install"
        _git(repo, "worktree", "add", "--no-checkout", "-qb", "linked", str(linked))

    monkeypatch.setattr("loadout.git_hooks.show_hooks", share)
    with pytest.raises(LoadoutError, match="effective hook directory changed"):
        install_hooks(repo)
    assert not (repo / ".git/hooks/pre-commit").exists()


def test_missing_loadout_on_path_explains_completed_checkout(repo, tmp_path_factory):
    _project(repo)
    _commit(repo, "loadout")
    _git(repo, "branch", "other")
    install_hooks(repo, regenerate=True)
    only_git = tmp_path_factory.mktemp("only-git")
    git_executable = subprocess.run(
        ["/bin/sh", "-c", "command -v git"], text=True, capture_output=True, check=True
    ).stdout.strip()
    (only_git / "git").symlink_to(git_executable)
    result = _git(repo, "checkout", "-q", "other", env={"PATH": str(only_git)}, check=False)
    assert result.returncode != 0
    assert "Git checkout already completed; loadout is missing from PATH" in result.stderr
    assert _git(repo, "branch", "--show-current").stdout == "other\n"


def test_uninstall_removes_loadout_hooks_and_leaves_a_foreign_one(repo, capsys):
    """`uninstall` deletes on the strength of the sentinel, so it must earn each delete."""
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    managed = repo / ".git/hooks/pre-commit"
    stale = repo / ".git/hooks/post-checkout"
    stale.write_bytes(managed.read_bytes().replace(b"pre-commit", b"post-checkout") + b"# edit\n")
    stale.chmod(0o755)
    foreign = repo / ".git/hooks/post-merge"
    foreign.write_text("#!/bin/sh\necho mine\n")
    foreign.chmod(0o755)

    statuses = {h.event: h.status for h in plan_hooks(repo, regenerate=True).hooks}
    assert statuses == {
        "pre-commit": "managed",
        "post-checkout": "stale",
        "post-merge": "occupied",
    }

    assert loadout.main(["integrate", "git-hooks", "uninstall", "--root", str(repo), "--yes"]) == 0
    assert not managed.exists()
    assert not stale.exists()
    assert foreign.read_text() == "#!/bin/sh\necho mine\n"
    assert "post-merge" not in capsys.readouterr().out


def test_uninstall_still_removes_a_hook_after_a_worktree_is_added(repo, tmp_path_factory):
    """Sharing arrives after installation: .git/hooks is common to every worktree, so a
    hook loadout wrote reclassifies with nothing touching it."""
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    hook = repo / ".git/hooks/pre-commit"
    written = hook.read_bytes()

    linked = tmp_path_factory.mktemp("added-later") / "linked"
    _git(repo, "worktree", "add", "--no-checkout", "-qb", "linked", str(linked))
    planned = plan_hooks(repo).hooks[0]
    assert (planned.status, planned.owned) == ("shared", True)
    assert hook.read_bytes() == written

    assert uninstall_hooks(repo, yes=True) == 0
    assert not hook.exists()


def test_uninstall_refuses_a_hooks_directory_outside_the_repository(repo, tmp_path_factory):
    _project(repo)
    _commit(repo, "loadout")
    outside = tmp_path_factory.mktemp("external-hooks")
    (outside / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    _git(repo, "config", "core.hooksPath", str(outside))

    assert plan_hooks(repo).hooks[0].status == "external"
    with pytest.raises(LoadoutError, match="outside this repository"):
        uninstall_hooks(repo, yes=True)
    assert (outside / "pre-commit").read_text() == "#!/bin/sh\nexit 0\n"


def test_status_reports_each_event_and_who_owns_it(repo, capsys):
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    (repo / ".git/hooks/post-merge").write_text("#!/bin/sh\necho mine\n")

    assert loadout.main(["integrate", "git-hooks", "status", "--root", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "pre-commit: installed by loadout" in out
    assert "post-checkout: not installed" in out
    assert "post-merge: present, not loadout's" in out


def test_a_generated_hook_runs_the_hidden_event_command(repo):
    """The command line is what fires at commit time; a hook file existing proves nothing."""
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo, regenerate=True, profile="focused")

    for event in ("pre-commit", "post-checkout", "post-merge"):
        body = (repo / ".git/hooks" / event).read_text()
        assert f"exec loadout hook-event {event} " in body
        assert "--profile focused" in body
        assert "git-hooks run" not in body


def test_uninstall_leaves_a_hook_belonging_to_another_loadout_source(repo):
    """One repository can hold several loadout sources but only one hook file. The
    sentinel alone says "loadout wrote this", not "this source owns it"."""
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    hook = repo / ".git/hooks/pre-commit"
    written = hook.read_bytes()

    nested = repo / "nested"
    _project(nested)
    planned = plan_hooks(nested).hooks[0]
    assert (planned.status, planned.owned) == ("occupied", False)

    assert uninstall_hooks(nested, yes=True) == 0
    assert hook.read_bytes() == written
    assert uninstall_hooks(repo, yes=True) == 0
    assert not hook.exists()


def test_an_unregistered_source_still_installs_hooks_for_the_default_profile(
    repo, fake_home, tmp_path_factory
):
    """The machine profile applies to the source it registers, not to every repository."""
    _project(repo)
    _commit(repo, "loadout")
    other = tmp_path_factory.mktemp("registered-elsewhere")
    machine_config_path().parent.mkdir(parents=True, exist_ok=True)
    machine_config_path().write_text(f'source="{other}"\nprofile="focused"\n')

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(repo)]) == 0
    assert "--profile default" in (repo / ".git/hooks/pre-commit").read_text()


def test_an_unusable_machine_config_does_not_block_a_repository_hook(repo, fake_home):
    _project(repo)
    _commit(repo, "loadout")
    machine_config_path().parent.mkdir(parents=True, exist_ok=True)
    machine_config_path().write_text('source="/nowhere/loadout"\n')

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(repo)]) == 0
    assert "--profile default" in (repo / ".git/hooks/pre-commit").read_text()
