from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.files import check_all
from loadout.git_hooks import (
    hook_content,
    install_hooks,
    plan_hooks,
    status_hooks,
    uninstall_hooks,
)
from loadout.machine import machine_config_path, resolve_profile
from loadout.staged import check_staged
from registration import register
from render_helpers import write_all
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
    (source / "config.toml").write_text('harnesses=["codex"]\ninstructions=["main"]\n')
    (source / "permissions.toml").write_text("")
    (source / "instructions").mkdir()
    (source / "instructions/main.md").write_text("original\n")
    return source


def test_the_fixture_project_is_one_sync_accepts(repo):
    """The hook tests never render it, so nothing else would notice it going stale."""
    _project(repo)

    assert loadout.main(["sync", "--root", str(repo)]) == 0
    assert (repo / "AGENTS.md").read_text().endswith("original\n")


def _commit(repo, *paths):
    _git(repo, "add", "--", *paths)
    return _git(repo, "commit", "-qm", "fixture")


def test_staged_ownership_record_matches_its_producer_and_allows_updates(repo):
    register(repo)
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
    register(repo)
    build(repo, {"model": "fixture"})
    write_all(repo)
    _commit(repo, "loadout.toml", "permissions.toml", "defaults")
    record = repo / "defaults/codex.owned"
    expected = record.read_text()
    assert check_staged(repo) == 0
    _git(repo, "rm", "defaults/codex.owned")
    assert [item.path for item in check_all(render_all(repo))] == [record]
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
    register(repo)
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
    root = register(repo / "nested/global")
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
    registration = fake_home / ".config/loadout/config.toml"
    assert sorted(fake_home.rglob("*")) == [
        registration.parent.parent,
        registration.parent,
        registration,
    ]
    _git(repo, "rm", "--cached", "nested/shared/instructions/rule.md")
    with pytest.raises(LoadoutError, match="snapshot is incomplete or invalid"):
        check_staged(root)


@pytest.mark.parametrize("old_source", ["absolute", "escaping-symlink"])
def test_head_source_migration_keeps_head_and_index_output_ownership(repo, fake_home, old_source):
    register(repo)
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
    register(repo)
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


def test_hook_installation_preserves_occupied_external_and_other_source_hooks(
    repo, tmp_path_factory
):
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
    outside = tmp_path_factory.mktemp("external-hooks")
    _git(repo, "config", "core.hooksPath", str(outside))
    assert plan_hooks(repo).placement == "external"
    install_hooks(repo)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("custom", [False, True])
@pytest.mark.parametrize("from_linked", [False, True])
def test_install_writes_a_hooks_directory_shared_with_linked_worktrees(
    repo, tmp_path_factory, capsys, custom, from_linked
):
    """Review 4, ruling on batch 1: the shared directory was preserved from every
    worktree with exit 0, so a repository with worktrees never got its hooks. The hook
    finds its worktree's top level when it runs, so one install serves them all."""
    _project(repo)
    _commit(repo, "loadout")
    directory = repo / ("custom-hooks" if custom else ".git/hooks")
    if custom:
        _git(repo, "config", "core.hooksPath", str(directory))
    linked = tmp_path_factory.mktemp("shared-main-hooks") / "linked"
    _git(repo, "worktree", "add", "-qb", "linked", str(linked))
    main_plan, linked_plan = plan_hooks(repo), plan_hooks(linked)
    assert main_plan.directory == linked_plan.directory == directory
    assert (main_plan.placement, main_plan.hooks[0].status) == ("shared", "install")

    assert install_hooks(linked if from_linked else repo) == 0

    out = capsys.readouterr().out
    assert f"Hooks directory {directory} is shared by every worktree of this repository" in out
    assert (directory / "pre-commit").read_bytes() == main_plan.hooks[0].content
    assert [plan_hooks(r).hooks[0].status for r in (repo, linked)] == ["managed", "managed"]
    assert status_hooks(repo) == 0
    assert "  shared with other worktrees of this repository\n" in capsys.readouterr().out


def test_a_worktree_specific_hooks_path_is_this_worktree_s_alone(repo, tmp_path_factory):
    _project(repo)
    _commit(repo, "loadout")
    linked = tmp_path_factory.mktemp("own-hooks") / "linked"
    _git(repo, "worktree", "add", "--no-checkout", "-qb", "linked", str(linked))
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(linked, "config", "--worktree", "core.hooksPath", ".linked-hooks")

    assert plan_hooks(repo).placement == "local"
    install_hooks(repo)
    assert plan_hooks(repo).hooks[0].status == "managed"


def test_standalone_install_rechecks_the_hooks_directory_after_preview(
    repo, tmp_path_factory, monkeypatch
):
    _project(repo)
    _commit(repo, "loadout")
    outside = tmp_path_factory.mktemp("late-external")

    def move(preview):
        assert preview["hooks"][0]["status"] == "install"
        _git(repo, "config", "core.hooksPath", str(outside))

    monkeypatch.setattr("loadout.git_hooks.show_hooks", move)
    with pytest.raises(LoadoutError, match="effective hook directory changed"):
        install_hooks(repo)
    assert not (repo / ".git/hooks/pre-commit").exists()
    assert list(outside.iterdir()) == []


def test_standalone_install_rechecks_a_directory_linked_away_after_preview(
    repo, tmp_path_factory, monkeypatch
):
    """The configured path, now a link out of the repository: git reports it resolved."""
    _project(repo)
    _commit(repo, "loadout")
    _git(repo, "config", "core.hooksPath", "custom-hooks")
    outside = tmp_path_factory.mktemp("linked-away")

    def link(preview):
        (repo / "custom-hooks").symlink_to(outside)

    monkeypatch.setattr("loadout.git_hooks.show_hooks", link)
    with pytest.raises(LoadoutError, match="effective hook directory changed"):
        install_hooks(repo)
    assert not (outside / "pre-commit").exists()


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
        "post-checkout": "update",
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
    plan = plan_hooks(repo)
    assert (plan.placement, plan.hooks[0].status, plan.hooks[0].owned) == (
        "shared",
        "managed",
        True,
    )
    assert hook.read_bytes() == written

    assert uninstall_hooks(repo, yes=True) == 0
    assert not hook.exists()


def test_uninstall_refuses_a_hooks_directory_outside_the_repository(repo, tmp_path_factory):
    _project(repo)
    _commit(repo, "loadout")
    outside = tmp_path_factory.mktemp("external-hooks")
    (outside / "pre-commit").write_text("#!/bin/sh\nexit 0\n")
    _git(repo, "config", "core.hooksPath", str(outside))

    assert plan_hooks(repo).placement == "external"
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
    install_hooks(repo, regenerate=True)

    for event in ("pre-commit", "post-checkout", "post-merge"):
        body = (repo / ".git/hooks" / event).read_text()
        # No --profile: the hook resolves it when it runs (review 2, 2.1).
        assert f'exec loadout hook-event {event} --root "$repo"/. -- "$@"\n' in body


def test_regenerate_rewrites_a_hook_loadout_wrote(repo, capsys):
    """A hook from before 2.1 bakes in its profile; `--regenerate` brings it up to date
    instead of calling loadout's own hook stale and asking for it to be merged by hand."""
    _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    hook = repo / ".git/hooks/pre-commit"
    current = hook.read_bytes()
    hook.write_bytes(current.replace(b' -- "$@"', b' --profile default -- "$@"'))
    capsys.readouterr()

    assert (
        loadout.main(["integrate", "git-hooks", "install", "--root", str(repo), "--regenerate"])
        == 0
    )

    assert "Git hook pre-commit: update" in capsys.readouterr().out
    assert hook.read_bytes() == current
    assert hook.stat().st_mode & 0o777 == 0o755


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


def test_an_unregistered_source_installs_hooks_that_run_the_default_profile(
    repo, fake_home, tmp_path_factory
):
    """The machine profile applies to the source it registers, not to every repository,
    and a hook resolves it when it runs."""
    _project(repo)
    _commit(repo, "loadout")
    other = tmp_path_factory.mktemp("registered-elsewhere")
    machine_config_path().parent.mkdir(parents=True, exist_ok=True)
    machine_config_path().write_text(f'source="{other}"\nprofile="focused"\n')

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(repo)]) == 0
    assert (repo / ".git/hooks/pre-commit").read_bytes() == hook_content("pre-commit", Path("."))
    assert resolve_profile(repo) == "default"


def test_an_unusable_machine_config_does_not_block_a_repository_hook(repo, fake_home):
    _project(repo)
    _commit(repo, "loadout")
    machine_config_path().parent.mkdir(parents=True, exist_ok=True)
    machine_config_path().write_text('source="/nowhere/loadout"\n')

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(repo)]) == 0
    assert (repo / ".git/hooks/pre-commit").read_bytes() == hook_content("pre-commit", Path("."))


def test_a_snapshot_of_five_thousand_entries_completes(repo, tmp_path):
    """Review 2, 1.9: every object id was written to `git cat-file --batch` before any
    reply was read, so once both pipes filled each side waited on the other. Run in a
    subprocess, so the deadlock fails the test rather than hanging the suite."""
    for index in range(5000):
        (repo / f"f{index:04}.txt").write_text(f"{index}\n" * 6)
    _git(repo, "add", ".")
    snapshot = tmp_path / "snapshot"
    script = (
        "import sys; from pathlib import Path; from loadout.git_tree import tree_entries, write_snapshot; "
        "root = Path(sys.argv[1]); write_snapshot(root, Path(sys.argv[2]), tree_entries(root))"
    )

    subprocess.run(
        [sys.executable, "-c", script, str(repo), str(snapshot)], timeout=120, check=True
    )

    assert len(list(snapshot.iterdir())) == 5000
    assert (snapshot / "f4999.txt").read_text() == "4999\n" * 6


def test_check_staged_finishes_with_thousands_of_generated_files(repo):
    """Each staged path was compared with every generated output, so a skill of 4,000
    files cost 16 million comparisons and the pre-commit hook ran for minutes."""
    _project(repo)
    skill = repo / "loadout/skills/big"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: big\ndescription: d\n---\nBig.\n")
    for index in range(4000):
        (skill / f"part-{index:04}.md").write_text(f"part {index}\n")
    _git(repo, "add", "loadout")

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, loadout; sys.exit(loadout.main(sys.argv[1:]))",
            "check",
            "--staged",
            "--root",
            str(repo),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert (result.returncode, result.stdout) == (
        0,
        "staged loadout dependency snapshot is valid\n",
    )


def test_check_staged_refuses_a_file_staged_inside_a_generated_folder(repo):
    """A skills directory is an output as a folder, so what is staged beneath it counts."""
    _project(repo)
    staged = repo / ".agents/skills/hand/SKILL.md"
    staged.parent.mkdir(parents=True)
    staged.write_text("---\nname: hand\ndescription: d\n---\nHand.\n")
    _git(repo, "add", "loadout", str(staged))

    with pytest.raises(
        LoadoutError, match=r"staged for addition or editing:\n  \.agents/skills/hand"
    ):
        check_staged(repo)


def test_a_plain_install_reports_an_old_loadout_hook_as_stale_and_leaves_it(repo, capsys):
    """Review 3, batch 5.8: the upgrade path after ca79d05. The marker and `--root` say
    the hook is loadout's; its body is an older one, which only `--regenerate` rewrites."""
    _project(repo)
    _commit(repo, "loadout")
    hook = repo / ".git/hooks/pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    old = (
        b"#!/bin/sh\n# loadout Git hook v1\nrepo=$(git rev-parse --show-toplevel) || exit $?\n"
        b'exec loadout check --staged --root "$repo"/. -- "$@"\n'
    )
    hook.write_bytes(old)
    hook.chmod(0o755)
    capsys.readouterr()

    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(repo)]) == 0

    out = capsys.readouterr().out
    assert f"Git hook pre-commit: stale ({hook})" in out
    assert "Written by loadout and out of date; `--regenerate` rewrites it." in out
    assert hook.read_bytes() == old


def test_install_into_an_external_directory_guards_each_command_by_repository(
    repo, tmp_path_factory, capsys
):
    """Review 5, batch 2.4: a global core.hooksPath runs in every repository, and the
    unguarded command blocked commits in all the others."""
    _project(repo)
    _commit(repo, "loadout")
    outside = tmp_path_factory.mktemp("global-hooks")
    _git(repo, "config", "core.hooksPath", str(outside))

    install_hooks(repo)

    out = capsys.readouterr().out
    assert "It may be shared by other repositories" in out
    plan = plan_hooks(repo)
    assert (
        f'  if [ "$(git rev-parse --path-format=absolute --git-common-dir)" = {plan.common_dir} ]; '
        f"then\n    repo=$(git rev-parse --show-toplevel) || exit $?\n"
        f"    {plan.hooks[0].command} || exit $?\n  fi\n"
    ) in out
    assert list(outside.iterdir()) == []
