from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

import loadout
from loadout.discovery import discover
from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.git_hooks import install_hooks, plan_hooks
from loadout.machine import machine_config_path
from loadout.migration import plan_migration
from loadout.migration_journal import Journal
from loadout.migration_transaction import (
    MigrationFailure,
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)
from loadout.staged import check_staged


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


def test_real_precommit_validates_partial_index_not_worktree(repo):
    source = _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    (source / "instructions.md").write_text("staged source\n")
    _git(repo, "add", "loadout/instructions.md")
    (source / "artifacts.toml").write_text("broken = [")
    result = _git(repo, "commit", "-qm", "valid staged source")
    assert "staged Loadout dependency snapshot is valid" in result.stderr
    _git(repo, "add", "loadout/artifacts.toml")
    (source / "artifacts.toml").write_text("artifact=[]\n")
    head = _git(repo, "rev-parse", "HEAD").stdout
    result = _git(repo, "commit", "-qm", "invalid staged source", check=False)
    assert result.returncode != 0
    assert "staged dependency snapshot is incomplete or invalid" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == head


def test_real_commit_honors_alternate_index_and_relative_index_from_nested_cwd(repo, monkeypatch):
    source = _project(repo)
    _commit(repo, "loadout")
    install_hooks(repo)
    alternate = repo / ".git/alternate"
    environment = {"GIT_INDEX_FILE": str(alternate)}
    _git(repo, "read-tree", "HEAD", env=environment)
    (source / "artifacts.toml").write_text("broken = [")
    _git(repo, "add", "loadout/artifacts.toml")
    (source / "instructions.md").write_text("alternate\n")
    _git(repo, "add", "loadout/instructions.md", env=environment)
    result = _git(repo, "commit", "-qm", "alternate source", env=environment)
    assert "snapshot is valid" in result.stderr
    assert _git(repo, "show", "HEAD:loadout/instructions.md").stdout == "alternate\n"
    monkeypatch.chdir(source)
    monkeypatch.setenv("GIT_INDEX_FILE", "../.git/alternate")
    assert check_staged(repo) == 0
    monkeypatch.setenv("GIT_INDEX_FILE", "../.git/missing")
    with pytest.raises(LoadoutError, match="supplied GIT_INDEX_FILE is missing"):
        check_staged(repo)


def test_unborn_empty_corrupt_and_unmerged_indexes(repo, monkeypatch):
    _project(repo)
    with pytest.raises(LoadoutError, match="no Loadout config in HEAD or the supplied index"):
        check_staged(repo)
    _git(repo, "add", "loadout")
    assert check_staged(repo) == 0
    empty = repo / ".git/empty-index"
    _git(repo, "read-tree", "--empty", env={"GIT_INDEX_FILE": str(empty)})
    monkeypatch.setenv("GIT_INDEX_FILE", str(empty))
    with pytest.raises(LoadoutError, match="no Loadout config"):
        check_staged(repo)
    empty.write_bytes(b"invalid index")
    with pytest.raises(LoadoutError, match="Git ls-files --stage failed"):
        check_staged(repo)
    monkeypatch.delenv("GIT_INDEX_FILE")
    oid = _git(repo, "rev-parse", ":loadout/config.toml").stdout.strip()
    result = subprocess.run(
        ["git", "-C", str(repo), "update-index", "--index-info"],
        input=f"100644 {oid} 1\tloadout/config.toml\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    with pytest.raises(LoadoutError, match="unmerged index entry"):
        check_staged(repo)


@pytest.mark.parametrize("retirement", ["declaration", "config", "source"])
def test_real_precommit_uses_head_ownership_after_declaration_removal(repo, retirement):
    source = _project(repo)
    output = repo / "AGENTS.md"
    output.write_text("original\n")
    _commit(repo, "loadout", "AGENTS.md")
    install_hooks(repo)
    if retirement == "declaration":
        (source / "artifacts.toml").write_text("artifact=[]\n")
        _git(repo, "add", "loadout/artifacts.toml")
    else:
        _git(repo, "rm", "-r", "loadout" if retirement == "source" else "loadout/config.toml")
    output.write_text("hand edit\n")
    _git(repo, "add", "AGENTS.md")
    head = _git(repo, "rev-parse", "HEAD").stdout
    result = _git(repo, "commit", "-qm", "remove owner and edit output", check=False)
    assert result.returncode != 0
    assert "generated files are staged for addition or editing" in result.stderr
    assert "AGENTS.md" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == head
    _git(repo, "rm", "--cached", "AGENTS.md")
    _git(repo, "commit", "-qm", "retire generated ownership")
    assert output.read_text() == "hand edit\n"


def test_required_source_deletion_does_not_read_unstaged_replacement(repo):
    source = _project(repo)
    _commit(repo, "loadout")
    _git(repo, "rm", "--cached", "loadout/instructions.md")
    assert (source / "instructions.md").read_text() == "original\n"
    with pytest.raises(LoadoutError, match="artifact source not found"):
        check_staged(repo)
    _git(repo, "add", "loadout/instructions.md")
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
    assert f"loadout template vendor {name}" in str(error.value)


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
    (repo / "permissions.toml").write_text("")
    other_input = 'order=["rule"]' if target == "instructions" else 'render="codex"\nrules=[]'
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


@pytest.mark.parametrize("kind", ["absolute", "symlink", "submodule"])
def test_external_and_submodule_dependencies_fail_without_reading_live_sources(
    repo, fake_home, kind
):
    source = _project(repo)
    if kind == "absolute":
        external = fake_home / "external"
        (external / "instructions").mkdir(parents=True)
        (external / "instructions/rule.md").write_text("private\n")
        (repo / "loadout.toml").write_text(
            f'[[source]]\nname="external"\npath="{external}"\n'
            '[instructions.external]\noutput="external.md"\norder=["rule"]\n'
        )
        _git(repo, "add", "loadout.toml")
    elif kind == "symlink":
        (source / "leak").symlink_to(fake_home, target_is_directory=True)
    _git(repo, "add", "loadout")
    if kind == "submodule":
        _git(repo, "commit", "-qm", "source")
        oid = _git(repo, "rev-parse", "HEAD").stdout.strip()
        _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{oid},loadout/skills/opaque")
    with pytest.raises(
        LoadoutError, match=r"absolute source path|escapes staged tree|submodule dependency"
    ):
        check_staged(repo)


def test_optional_private_source_is_absent_and_programs_are_not_executed(repo):
    source = _project(repo)
    (source / "artifacts.toml").write_text(
        '[[artifact]]\nagents=["codex"]\nformat="copy"\ncategory="support"\n'
        'output="copied.sh"\nsource="program.sh"\n'
        '[[artifact]]\nagents=["codex"]\nformat="json"\noutput="private.json"\n'
        '[artifact.parts.settings]\nsource="private.json"\noptional=true\n'
    )
    program = source / "program.sh"
    program.write_text(f'#!/bin/sh\ntouch "{repo}/executed"\n')
    program.chmod(0o755)
    _git(repo, "add", "loadout")
    (source / "private.json").write_text("not valid JSON")
    assert check_staged(repo) == 0
    assert sorted(p.name for p in repo.iterdir()) == [".git", "loadout"]


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
    assert plan_hooks(repo).hooks[0].status == "shared-or-external"
    install_hooks(repo)
    assert list(shared.iterdir()) == []


def test_linked_worktree_validates_supplied_index_and_preserves_common_hooks(
    repo, tmp_path_factory
):
    _project(repo)
    _commit(repo, "loadout")
    linked = tmp_path_factory.mktemp("linked-parent") / "worktree"
    _git(repo, "worktree", "add", "-qb", "linked", str(linked))
    assert plan_hooks(linked).hooks[0].status == "shared-or-external"
    assert check_staged(linked) == 0
    _git(linked, "config", "extensions.worktreeConfig", "true")
    _git(linked, "config", "--worktree", "core.hooksPath", ".local-hooks")
    install_hooks(linked)
    (linked / "loadout/instructions.md").write_text("linked source\n")
    _commit(linked, "loadout/instructions.md")
    assert _git(linked, "show", "HEAD:loadout/instructions.md").stdout == "linked source\n"
    assert _git(repo, "show", "HEAD:loadout/instructions.md").stdout == "original\n"


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
    assert main_plan.hooks[0].status == "shared-or-external"
    assert linked_plan.hooks[0].status == "shared-or-external"
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


@pytest.mark.parametrize("boundary", ["apply", "resume", "recovery"])
def test_migration_rechecks_hook_sharing_at_mutation_boundaries(
    repo, tmp_path_factory, monkeypatch, boundary
):
    _project(repo)
    _commit(repo, "loadout")
    plan = plan_migration(discover(repo, scope="project", agents=("codex",)))
    prepared = prepare_migration(plan, hooks=plan_hooks(repo, regenerate=True))
    assert [hook.status for hook in prepared.hooks.hooks] == ["install"] * 3
    path = None
    if boundary != "apply":
        original = Journal.step

        def interrupt(self):
            if self.operations[self.next].path.name == "post-checkout":
                raise OSError("fixture interruption before second hook")
            original(self)

        monkeypatch.setattr(Journal, "step", interrupt)
        with pytest.raises(MigrationFailure, match="fixture interruption") as error:
            apply_migration(prepared)
        path = error.value.journal
        monkeypatch.setattr(Journal, "step", original)
        assert (repo / ".git/hooks/pre-commit").read_bytes() == prepared.hooks.hooks[0].content
    linked = tmp_path_factory.mktemp("migration-sharing") / "linked"
    _git(repo, "worktree", "add", "--no-checkout", "-qb", "linked", str(linked))
    before = {p.name: p.read_bytes() for p in (repo / ".git/hooks").iterdir()}
    with pytest.raises(LoadoutError, match=r"[Gg]it hook.*(changed|installation plan)"):
        if boundary == "apply":
            apply_migration(prepared)
        elif boundary == "resume":
            resume_migration(path)
        else:
            recover_migration(path)
    assert {p.name: p.read_bytes() for p in (repo / ".git/hooks").iterdir()} == before


def test_init_hook_preview_and_new_git_metadata_are_transactional(
    repo, tmp_path_factory, capsys, monkeypatch
):
    new = tmp_path_factory.mktemp("new-repository")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "Fixture")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "user.email")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "fixture@example.invalid")
    (new / "AGENTS.md").write_text("initial\n")
    args = [
        "init",
        "--project",
        "--harness",
        "codex",
        "--root",
        str(new),
        "--git-hooks",
        "regenerate",
    ]
    assert loadout.main([*args, "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert {h["status"] for h in preview["git_hooks"]["hooks"]} == {"install"}
    assert not (new / ".git").exists()
    assert loadout.main([*args, "--yes", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert _git(new, "show", "HEAD:AGENTS.md").stdout == "initial\n"
    journal = Path(result["journal"])
    assert Journal.load(journal).status == "complete"
    baseline = _git(new, "rev-parse", "HEAD").stdout
    recovered = recover_migration(journal)
    assert recovered.conflicts == ()
    assert _git(new, "rev-parse", "HEAD").stdout == baseline
    assert (new / "AGENTS.md").read_text() == "initial\n"
    assert all(
        not (new / ".git/hooks" / event).exists()
        for event in ("pre-commit", "post-checkout", "post-merge")
    )


def test_hook_failure_can_resume_and_external_hook_is_preserved_by_recovery(repo, monkeypatch):
    (repo / "AGENTS.md").write_text("initial\n")
    plan = plan_migration(discover(repo, scope="project", agents=("codex",)))
    prepared = prepare_migration(plan, hooks=plan_hooks(repo, regenerate=True))
    original = Journal.step

    def fail(self):
        if self.operations[self.next].path.name == "post-checkout":
            raise OSError("fixture interruption")
        original(self)

    monkeypatch.setattr(Journal, "step", fail)
    with pytest.raises(MigrationFailure) as error:
        apply_migration(prepared)
    journal = error.value.journal
    assert (repo / ".git/hooks/pre-commit").is_file()
    monkeypatch.setattr(Journal, "step", original)
    resume_migration(journal)
    hook = repo / ".git/hooks/post-merge"
    hook.write_text("external edit\n")
    recovered = recover_migration(journal)
    assert recovered.conflicts == (hook,)
    assert hook.read_text() == "external edit\n"


def test_global_init_hooks_keep_selected_source_and_registered_profile(repo, fake_home, capsys):
    assert (
        loadout.main(["init", "--global", "--source", str(repo), "--harness", "codex", "--yes"])
        == 0
    )
    machine = machine_config_path()
    machine.write_text(f'source="{repo / "loadout"}"\nprofile="focused"\n')
    (repo / "loadout/focused.toml").write_text('extends="default"\n')
    capsys.readouterr()
    assert (
        loadout.main(
            [
                "init",
                "--global",
                "--source",
                str(repo),
                "--git-hooks",
                "regenerate",
                "--yes",
                "--json",
            ]
        )
        == 0
    )
    assert "--profile focused" in (repo / ".git/hooks/post-checkout").read_text()
    assert '"$repo"/loadout' in (repo / ".git/hooks/pre-commit").read_text()
    assert json.loads(capsys.readouterr().out)["already_initialized"]


def test_actual_checkout_and_merge_regenerate_or_preserve_edits_after_git_completed(repo):
    source = _project(repo)
    _commit(repo, "loadout")
    first = _git(repo, "rev-parse", "HEAD").stdout
    _git(repo, "checkout", "-qb", "changed")
    (source / "instructions.md").write_text("changed branch\n")
    _commit(repo, "loadout/instructions.md")
    changed = _git(repo, "rev-parse", "HEAD").stdout
    _git(repo, "checkout", "-q", "main")
    assert loadout.main(["sync", "--root", str(repo)]) == 0
    assert (repo / "AGENTS.md").read_text() == "original\n"
    install_hooks(repo, regenerate=True)
    result = _git(repo, "checkout", "-q", "changed")
    assert "wrote AGENTS.md" in result.stderr
    assert (repo / "AGENTS.md").read_text() == "changed branch\n"
    (repo / "AGENTS.md").write_text("manual edit\n")
    result = _git(repo, "checkout", "-q", "main", check=False)
    assert result.returncode != 0
    assert "Git checkout already completed" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == first
    assert (repo / "AGENTS.md").read_text() == "manual edit\n"
    result = _git(repo, "merge", "--ff-only", "changed", check=False)
    assert result.returncode == 0
    assert "Git merge already completed" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == changed
    assert (repo / "AGENTS.md").read_text() == "manual edit\n"


def test_post_checkout_file_event_does_not_regenerate(repo):
    source = _project(repo)
    _commit(repo, "loadout")
    assert loadout.main(["sync", "--root", str(repo)]) == 0
    install_hooks(repo, regenerate=True)
    (repo / "AGENTS.md").write_text("manual edit\n")
    (source / "instructions.md").write_text("unstaged\n")
    _git(repo, "checkout", "--", "loadout/instructions.md")
    assert (source / "instructions.md").read_text() == "original\n"
    assert (repo / "AGENTS.md").read_text() == "manual edit\n"


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


def test_hook_journal_rejects_tampered_script_and_recovers_before_git_init(
    repo, tmp_path_factory, monkeypatch
):
    new = tmp_path_factory.mktemp("init-failure")
    (new / "AGENTS.md").write_text("initial\n")
    plan = plan_migration(discover(new, scope="project", agents=("codex",)))
    prepared = prepare_migration(plan, hooks=plan_hooks(new, initialize=new))

    def failure(*args):
        raise OSError("Git init fixture failure")

    monkeypatch.setattr("loadout.migration_git.checkpoint", failure)
    with pytest.raises(MigrationFailure) as error:
        apply_migration(prepared)
    path = error.value.journal
    assert not (new / ".git").exists()
    journal = Journal.load(path)
    original = journal.operations
    journal.operations = tuple(
        replace(operation, after=replace(operation.after, content=b"evil\n"))
        if operation.phase == "git-hook"
        else operation
        for operation in original
    )
    journal.save()
    with pytest.raises(LoadoutError, match="journal Git hook escapes"):
        Journal.load(path)
    journal.operations = original
    journal.save()
    assert recover_migration(path).conflicts == ()
    assert not (new / ".git").exists()
    assert (new / "AGENTS.md").read_text() == "initial\n"
