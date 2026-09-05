from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from loadout import migration_git, migration_journal, migration_transaction
from loadout.artifacts import ArtifactScope
from loadout.commands import cmd_check, cmd_sync
from loadout.discovery import discover
from loadout.errors import LoadoutError
from loadout.migration import plan_migration
from loadout.migration_models import MigrationPlan, RootMapping, SourceWrite
from loadout.migration_transaction import (
    MigrationFailure,
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)


def write(root: Path, name: str, text: str, mode: int = 0o644) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def repository(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.name", "Migration Test")
    git(root, "config", "user.email", "migration@example.invalid")
    git(root, "config", "commit.gpgsign", "false")


def plan(
    root: Path,
    *,
    scope: ArtifactScope = "project",
    agents: tuple[str, ...] = ("claude",),
    mappings: tuple[RootMapping, ...] = (),
) -> MigrationPlan:
    result = plan_migration(discover(root, scope=scope, agents=agents, mappings=mappings))
    assert result.complete, result.preview()
    return result


def test_project_migration_checkpoints_stages_and_uses_normal_lifecycle(tmp_path: Path) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    original = settings.read_bytes()
    prepared = prepare_migration(plan(tmp_path))
    assert prepared.preview()["git"]["baseline_paths"] == [".claude/settings.json"]
    result = apply_migration(prepared)
    assert result.baseline == git(tmp_path, "rev-parse", "HEAD").decode().strip()
    assert git(tmp_path, "show", "HEAD:.claude/settings.json") == original
    assert json.loads(settings.read_bytes()) == {"model": "before"}
    staged = git(tmp_path, "diff", "--cached", "--name-only").decode().splitlines()
    assert ".claude/settings.json" in staged
    assert "loadout/config.toml" in staged
    assert git(tmp_path, "ls-files", "--", ".claude/settings.json") == b""
    assert cmd_check(tmp_path) == 0
    source = next(
        w.path
        for w in prepared.plan.source_writes
        if w.path.is_relative_to(prepared.plan.inventory.source_root / "settings")
        and b'"before"' in w.content
    )
    source.write_text('{"model":"after"}')
    assert cmd_sync(tmp_path) == 0
    assert json.loads(settings.read_bytes()) == {"model": "after"}
    assert cmd_check(tmp_path) == 0


def test_global_migration_preserves_foreign_fields_and_registers_explicit_write(
    tmp_path: Path, fake_home: Path
) -> None:
    repository(tmp_path)
    settings = write(
        fake_home,
        ".pi/agent/settings.json",
        '{"defaultModel":"before","lastChangelogVersion":"1"}',
        0o640,
    )
    registration = SourceWrite(
        fake_home / ".config/loadout/config.toml",
        f'root = "{tmp_path / "loadout"}"\n'.encode(),
        0o600,
        True,
    )
    prepared = prepare_migration(
        plan(tmp_path, scope="global", agents=("pi",)), machine_write=registration
    )
    apply_migration(prepared)
    assert json.loads(settings.read_bytes()) == {
        "defaultModel": "before",
        "lastChangelogVersion": "1",
    }
    assert settings.stat().st_mode & 0o777 == 0o640
    assert registration.path.read_bytes() == registration.content
    assert cmd_check(tmp_path / "loadout") == 0


def test_unrelated_partial_index_and_ignore_hunks_survive(tmp_path: Path) -> None:
    repository(tmp_path)
    unrelated = write(tmp_path, "app.txt", "base\n")
    write(tmp_path, ".gitignore", "base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    unrelated.write_text("staged\n")
    write(tmp_path, ".gitignore", "base\nstaged-ignore\n")
    git(tmp_path, "add", "app.txt", ".gitignore")
    unrelated.write_text("worktree\n")
    write(tmp_path, ".gitignore", "base\nstaged-ignore\nworktree-ignore\n")
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    apply_migration(prepare_migration(plan(tmp_path)))
    assert git(tmp_path, "show", ":app.txt") == b"staged\n"
    assert unrelated.read_bytes() == b"worktree\n"
    assert git(tmp_path, "show", "HEAD:app.txt") == b"base\n"
    indexed = git(tmp_path, "show", ":.gitignore").decode().splitlines()
    assert indexed[:2] == ["base", "staged-ignore"]
    assert "worktree-ignore" not in indexed
    assert (tmp_path / ".gitignore").read_text().splitlines()[:3] == [
        "base",
        "staged-ignore",
        "worktree-ignore",
    ]


def test_directory_symlink_retains_runtime_visibility_and_external_targets(
    tmp_path: Path, fake_home: Path
) -> None:
    repository(tmp_path)
    canonical = fake_home / "canonical"
    settings = write(canonical, "settings.json", '{"model":"before"}')
    auth = write(canonical, ".credentials.json", '{"token":"test-runtime"}', 0o600)
    destination = fake_home / ".claude"
    destination.symlink_to(canonical, target_is_directory=True)
    migration = plan(
        tmp_path, scope="global", mappings=(RootMapping(canonical, destination, ("claude",)),)
    )
    prepared = prepare_migration(migration)
    apply_migration(prepared)
    assert destination.is_dir() and not destination.is_symlink()
    assert (destination / ".credentials.json").read_bytes() == auth.read_bytes()
    assert (destination / ".credentials.json").is_symlink()
    assert settings.read_bytes() == b'{"model":"before"}'
    assert cmd_check(tmp_path / "loadout") == 0


@pytest.mark.parametrize("mutation", ["source", "index", "head"])
def test_stale_preparation_is_rejected_before_journal(tmp_path: Path, mutation: str) -> None:
    repository(tmp_path)
    path = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(tmp_path))
    if mutation == "source":
        path.write_text('{"model":"edited"}')
    elif mutation == "index":
        git(tmp_path, "add", ".claude/settings.json")
    else:
        git(tmp_path, "commit", "--allow-empty", "-qm", "concurrent")
    with pytest.raises(Exception, match="changed"):
        apply_migration(prepared)
    assert not (tmp_path / ".loadout-state").exists()


def test_hook_failure_has_journal_and_does_not_write_source(tmp_path: Path) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    write(tmp_path, ".git/hooks/pre-commit", "#!/bin/sh\nexit 1\n", 0o755)
    prepared = prepare_migration(plan(tmp_path))
    with pytest.raises(MigrationFailure) as failure:
        apply_migration(prepared)
    assert failure.value.journal.is_file()
    assert settings.read_bytes() == b'{"model":"before"}'
    assert not (tmp_path / "loadout").exists()
    assert recover_migration(failure.value.journal).conflicts == ()


def test_existing_source_noop_can_write_explicit_registration(
    tmp_path: Path, fake_home: Path
) -> None:
    repository(tmp_path)
    apply_migration(prepare_migration(plan(tmp_path)))
    existing = plan(tmp_path)
    assert existing.already_initialized
    assert apply_migration(prepare_migration(existing)).journal is None
    write = SourceWrite(
        fake_home / ".config/loadout/config.toml", b'root = "chosen"\n', 0o600, True
    )
    result = apply_migration(prepare_migration(existing, machine_write=write))
    assert result.already_initialized
    assert write.path.read_bytes() == write.content
    assert result.journal is not None
    assert resume_migration(result.journal) == result


def test_new_repository_checkpoint_filters_private_namespaces_and_ignored_files(
    tmp_path: Path, fake_home: Path
) -> None:
    write(
        fake_home,
        ".gitconfig",
        "[user]\nname = Migration Test\nemail = migration@example.invalid\n[commit]\ngpgsign = false\n",
    )
    write(tmp_path, "app.txt", "app\n")
    for path in ("src/projects/model.py", "src/debug/trace.py", "tests/history/test_events.py"):
        write(tmp_path, path, "ordinary source\n")
    write(tmp_path, ".gitignore", "ignored.txt\n")
    write(tmp_path, "ignored.txt", "ignored\n")
    write(tmp_path, ".claude/settings.json", '{"model":"shared"}')
    write(tmp_path, ".claude/settings.local.json", '{"model":"personal"}')
    write(tmp_path, ".claude/.credentials.json", '{"token":"private-token"}')
    write(tmp_path, ".ssh/id_ed25519", "private key\n")
    write(tmp_path, ".env", "TOKEN=secret\n")
    prepared = prepare_migration(plan(tmp_path))
    assert prepared.git is not None
    assert prepared.git.baseline == (
        ".claude/settings.json",
        ".gitignore",
        "app.txt",
        "src/debug/trace.py",
        "src/projects/model.py",
        "tests/history/test_events.py",
    )
    apply_migration(prepared)
    assert git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines() == list(
        prepared.git.baseline
    )
    staged = git(tmp_path, "ls-files").decode().splitlines()
    assert "app.txt" in staged
    assert "loadout/config.toml" in staged
    assert git(tmp_path, "show", ":.gitignore").startswith(b"ignored.txt\n")
    assert all("local/" not in p and ".loadout-state" not in p for p in staged)
    private = next(w for w in prepared.plan.source_writes if w.private and w.content)
    assert private.path.parent.stat().st_mode & 0o777 == 0o700


def test_new_repository_cannot_start_at_home(fake_home: Path) -> None:
    write(fake_home, ".claude/settings.json", '{"model":"shared"}')
    with pytest.raises(LoadoutError, match="dedicated directory"):
        prepare_migration(plan(fake_home, scope="global"))
    assert not (fake_home / ".git").exists()


def test_nested_global_source_uses_enclosing_repository_only(
    tmp_path: Path, fake_home: Path
) -> None:
    repository(tmp_path)
    write(tmp_path, "unrelated.txt", "base\n")
    git(tmp_path, "add", "unrelated.txt")
    git(tmp_path, "commit", "-qm", "base")
    nested = tmp_path / "dotfiles/agents"
    write(nested, "claude/settings.json", '{"model":"source"}')
    prepared = prepare_migration(
        plan(
            nested,
            scope="global",
            mappings=(RootMapping(nested / "claude", fake_home / ".claude", ("claude",)),),
        )
    )
    assert prepared.git is not None
    assert prepared.git.root == tmp_path
    assert prepared.git.baseline == ("dotfiles/agents/claude/settings.json",)
    apply_migration(prepared)
    assert (nested / ".git").exists() is False
    assert git(tmp_path, "show", "HEAD:unrelated.txt") == b"base\n"
    assert "dotfiles/agents/loadout/loadout.toml" in git(tmp_path, "ls-files").decode().splitlines()


@pytest.mark.parametrize("detached", [False, True])
def test_existing_checkpoint_changes_only_adopted_index_entries(
    tmp_path: Path, detached: bool
) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"base"}')
    write(tmp_path, "unrelated.txt", "base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    if detached:
        git(tmp_path, "checkout", "--detach", "-q")
    settings.write_text('{"model":"checkpoint"}')
    write(tmp_path, "unrelated.txt", "staged\n")
    git(tmp_path, "add", "unrelated.txt")
    write(tmp_path, "unrelated.txt", "worktree\n")
    original_ref = migration_git.identity(tmp_path)[1]
    apply_migration(prepare_migration(plan(tmp_path)))
    assert migration_git.identity(tmp_path)[1] == original_ref
    assert git(tmp_path, "show", "HEAD:.claude/settings.json") == b'{"model":"checkpoint"}'
    assert git(tmp_path, "show", "HEAD:unrelated.txt") == b"base\n"
    assert git(tmp_path, "show", ":unrelated.txt") == b"staged\n"


def test_file_and_directory_symlinks_checkpoint_link_entries_and_retire_only_originals(
    tmp_path: Path,
) -> None:
    repository(tmp_path)
    original = write(tmp_path, "canonical/settings.json", '{"model":"before"}')
    write(tmp_path, "canonical/.credentials.json", '{"token":"kept"}')
    (tmp_path / ".claude").symlink_to("canonical", target_is_directory=True)
    prepared = prepare_migration(plan(tmp_path))
    assert prepared.git is not None
    assert prepared.git.baseline == (".claude", "canonical/settings.json")
    apply_migration(prepared)
    assert git(tmp_path, "ls-tree", "HEAD", ".claude").startswith(b"120000 blob")
    assert original.exists() is False
    assert (tmp_path / "canonical/.credentials.json").read_bytes() == b'{"token":"kept"}'
    assert (tmp_path / ".claude/.credentials.json").read_bytes() == b'{"token":"kept"}'
    assert cmd_check(tmp_path) == 0


@pytest.mark.parametrize("private", [False, True])
def test_partially_staged_adopted_files_require_resolution_even_when_private(
    tmp_path: Path, private: bool
) -> None:
    repository(tmp_path)
    name = ".claude/settings.local.json" if private else ".claude/settings.json"
    target = write(tmp_path, name, '{"model":"base"}')
    git(tmp_path, "add", name)
    git(tmp_path, "commit", "-qm", "base")
    target.write_text('{"model":"staged"}')
    git(tmp_path, "add", name)
    target.write_text('{"model":"worktree"}')
    with pytest.raises(LoadoutError, match="partially staged adopted"):
        prepare_migration(plan(tmp_path))
    assert git(tmp_path, "show", ":" + name) == b'{"model":"staged"}'


def test_interrupted_deployment_resumes_frozen_bytes_and_normal_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(tmp_path))
    real = migration_journal.install

    def interrupt(path: Path, image: migration_journal.Image) -> None:
        real(path, image)
        if path == settings:
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(migration_journal, "install", interrupt)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    journal = migration_journal.Journal.load(failure.value.journal)
    assert journal.pending
    baseline = git(tmp_path, "rev-parse", "HEAD")
    result = resume_migration(failure.value.journal)
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert result.baseline == baseline.decode().strip()
    assert cmd_check(tmp_path) == 0


def test_recovery_preserves_conflicting_edits_and_successful_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(tmp_path))

    def fail(journal: migration_journal.Journal) -> None:
        raise OSError("injected staging failure")

    with monkeypatch.context() as patch:
        patch.setattr(migration_transaction, "_finish", fail)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    baseline = git(tmp_path, "rev-parse", "HEAD")
    settings.write_text('{"model":"concurrent"}')
    recovery = recover_migration(failure.value.journal)
    assert settings in recovery.conflicts
    assert settings.read_bytes() == b'{"model":"concurrent"}'
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert git(tmp_path, "show", "HEAD:.claude/settings.json") == b'{"model":"before"}'


def test_failure_after_checkpoint_commit_records_and_refreshes_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(tmp_path))

    def fail(*args: object) -> bytes:
        raise OSError("injected refresh failure")

    with monkeypatch.context() as patch:
        patch.setattr(migration_git, "refresh_index", fail)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    baseline = git(tmp_path, "rev-parse", "HEAD")
    recorded = migration_journal.Journal.load(failure.value.journal)
    assert recorded.metadata["baseline"] == baseline.decode().strip()
    recovery = recover_migration(failure.value.journal)
    assert recovery.conflicts == ()
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert git(tmp_path, "diff", "--cached", "--name-only") == b""


def test_resume_after_hook_failure_refuses_changed_checkpoint_content(tmp_path: Path) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    hook = write(tmp_path, ".git/hooks/pre-commit", "#!/bin/sh\nexit 1\n", 0o755)
    with pytest.raises(MigrationFailure) as failure:
        apply_migration(prepare_migration(plan(tmp_path)))
    hook.unlink()
    settings.write_text('{"env":{"PRIVATE_TOKEN":"changed-secret"}}')
    with pytest.raises(MigrationFailure, match="input changed"):
        resume_migration(failure.value.journal)
    assert migration_git.identity(tmp_path)[0] is None


@pytest.mark.parametrize("state", ["public", "tracked", "symlink"])
def test_untrusted_recovery_state_is_rejected_before_checkpoint(tmp_path: Path, state: str) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    directory = tmp_path / ".loadout-state"
    if state == "symlink":
        directory.symlink_to(tmp_path / "other")
    else:
        directory.mkdir(mode=0o755 if state == "public" else 0o700)
        if state == "tracked":
            write(directory, "captured.json", "{}", 0o600)
            git(tmp_path, "add", ".loadout-state/captured.json")
    with pytest.raises(LoadoutError):
        prepare_migration(plan(tmp_path))
    assert migration_git.identity(tmp_path)[0] is None


def test_unresolved_plan_cannot_be_prepared(tmp_path: Path) -> None:
    resolved = plan(tmp_path)
    with pytest.raises(LoadoutError, match="fully resolved"):
        prepare_migration(replace(resolved, validated=False))


def test_first_skill_entry_edit_and_last_deletion_use_adopted_receipt(tmp_path: Path) -> None:
    repository(tmp_path)
    apply_migration(prepare_migration(plan(tmp_path)))
    records = tomllib.loads((tmp_path / "loadout/artifacts.toml").read_text())["artifact"]
    skills = next(record for record in records if record.get("category") == "skills")
    source = write(tmp_path / "loadout" / skills["source"], "first/SKILL.md", "first\n")
    destination = tmp_path / ".claude/skills/first/SKILL.md"
    assert cmd_sync(tmp_path) == 0
    assert destination.read_text() == "first\n"
    source.write_text("edited\n")
    assert cmd_sync(tmp_path) == 0
    assert destination.read_text() == "edited\n"
    source.unlink()
    assert cmd_sync(tmp_path) == 0
    assert destination.exists() is False
    assert cmd_check(tmp_path) == 0


def test_partial_document_symlink_freezes_foreign_bytes_before_replacement(
    tmp_path: Path, fake_home: Path
) -> None:
    repository(tmp_path)
    original = write(
        tmp_path,
        "canonical/config.toml",
        '# runtime comment\nmodel = "before"\n[projects."/private"]\ntrust_level = "trusted"\n',
        0o640,
    )
    destination = fake_home / ".codex/config.toml"
    destination.parent.mkdir()
    destination.symlink_to(original)
    prepared = prepare_migration(
        plan(
            tmp_path,
            scope="global",
            agents=("codex",),
            mappings=(RootMapping(original.parent, destination.parent, ("codex",)),),
        )
    )
    original_bytes = original.read_bytes()
    apply_migration(prepared)
    assert destination.is_symlink() is False
    assert destination.read_bytes() == original_bytes
    assert original.read_bytes() == original_bytes
    assert destination.stat().st_mode & 0o777 == 0o640
    assert cmd_check(tmp_path / "loadout") == 0


def test_linked_worktree_preserves_other_checkout_index(tmp_path: Path) -> None:
    main = tmp_path / "main"
    repository(main)
    write(main, "app.txt", "base\n")
    git(main, "add", "app.txt")
    git(main, "commit", "-qm", "base")
    worktree = tmp_path / "linked"
    git(main, "worktree", "add", "--detach", str(worktree))
    original_index = (main / ".git/index").read_bytes()
    write(worktree, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(worktree))
    assert prepared.git is not None
    assert prepared.git.index_path != worktree / ".git/index"
    apply_migration(prepared)
    assert (main / ".git/index").read_bytes() == original_index
    assert cmd_check(worktree) == 0


def test_identity_failure_keeps_initial_files_and_can_resume_after_configuration(
    tmp_path: Path, fake_home: Path
) -> None:
    write(fake_home, ".gitconfig", "[user]\nuseConfigOnly = true\n[commit]\ngpgsign = false\n")
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    with pytest.raises(MigrationFailure, match="identity") as failure:
        apply_migration(prepare_migration(plan(tmp_path)))
    assert settings.read_bytes() == b'{"model":"before"}'
    assert migration_git.identity(tmp_path)[0] is None
    git(tmp_path, "config", "user.name", "Migration Test")
    git(tmp_path, "config", "user.email", "migration@example.invalid")
    resume_migration(failure.value.journal)
    assert cmd_check(tmp_path) == 0


def test_new_repository_resume_refuses_an_intervening_user_commit(
    tmp_path: Path, fake_home: Path
) -> None:
    write(fake_home, ".gitconfig", "[user]\nuseConfigOnly = true\n[commit]\ngpgsign = false\n")
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    with pytest.raises(MigrationFailure) as failure:
        apply_migration(prepare_migration(plan(tmp_path)))
    git(tmp_path, "config", "user.name", "Migration Test")
    git(tmp_path, "config", "user.email", "migration@example.invalid")
    write(tmp_path, "app.txt", "user work\n")
    git(tmp_path, "add", "app.txt")
    git(tmp_path, "commit", "-qm", "user work")
    head = git(tmp_path, "rev-parse", "HEAD")
    index = (tmp_path / ".git/index").read_bytes()
    with pytest.raises(MigrationFailure, match="does not match"):
        resume_migration(failure.value.journal)
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert (tmp_path / ".git/index").read_bytes() == index


def test_literal_metacharacter_paths_do_not_expand_staging_or_ignore_rules(tmp_path: Path) -> None:
    repository(tmp_path)
    name = ".claude/commands/[one]* ?.md"
    output = write(tmp_path, name, "literal\n")
    other = write(tmp_path, ".claude/commands/one-two.md", "other\n")
    prepared = prepare_migration(plan(tmp_path))
    apply_migration(prepared)
    assert git(tmp_path, "show", "HEAD:" + name) == b"literal\n"
    assert git(tmp_path, "show", "HEAD:.claude/commands/one-two.md") == b"other\n"
    assert output.read_text() == "literal\n"
    assert other.read_text() == "other\n"
    assert git(tmp_path, "check-ignore", "--", name).decode().strip() == name
    assert cmd_check(tmp_path) == 0


def test_newline_output_is_rejected_before_git_initialization(tmp_path: Path) -> None:
    write(tmp_path, "a\nb/CLAUDE.md", "literal\n")
    with pytest.raises(LoadoutError, match="newlines"):
        prepare_migration(plan(tmp_path))
    assert (tmp_path / ".git").exists() is False


def test_recovery_after_final_staging_keeps_baseline_and_restores_original_entries(
    tmp_path: Path,
) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    result = apply_migration(prepare_migration(plan(tmp_path)))
    assert result.journal is not None
    baseline = git(tmp_path, "rev-parse", "HEAD")
    recovery = recover_migration(result.journal)
    assert recovery.conflicts == ()
    assert settings.read_bytes() == b'{"model":"before"}'
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert git(tmp_path, "diff", "--cached", "--name-only") == b""
    assert recover_migration(result.journal).conflicts == ()


def test_recovery_after_user_commit_preserves_committed_source_and_index(tmp_path: Path) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    result = apply_migration(prepare_migration(plan(tmp_path)))
    assert result.journal is not None
    git(tmp_path, "commit", "-qm", "user adopted migration")
    head = git(tmp_path, "rev-parse", "HEAD")
    index = (tmp_path / ".git/index").read_bytes()
    source = (tmp_path / "loadout/config.toml").read_bytes()
    recovery = recover_migration(result.journal)
    assert recovery.conflicts == (tmp_path / ".git/index",)
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert (tmp_path / ".git/index").read_bytes() == index
    assert (tmp_path / "loadout/config.toml").read_bytes() == source


def test_source_privacy_change_in_info_exclude_blocks_preparation(tmp_path: Path) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    migration = plan(tmp_path)
    write(tmp_path, ".git/info/exclude", ".claude/settings.json\n")
    with pytest.raises(LoadoutError, match="privacy changed"):
        prepare_migration(migration)


def test_journal_is_ignored_during_the_real_checkpoint_hook(tmp_path: Path) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    write(
        tmp_path,
        ".git/hooks/pre-commit",
        '#!/bin/sh\nfor path in .loadout-state/migrations/*/journal.json; do\n  git check-ignore -q -- "$path" || exit 17\ndone\n',
        0o755,
    )
    apply_migration(prepare_migration(plan(tmp_path)))
    assert git(tmp_path, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines() == [
        ".claude/settings.json"
    ]


@pytest.mark.parametrize("tamper", ["cursor", "phase", "scope"])
def test_loaded_journal_rejects_invalid_shape_before_recovery(tmp_path: Path, tamper: str) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    result = apply_migration(prepare_migration(plan(tmp_path)))
    assert result.journal is not None
    raw = json.loads(result.journal.read_bytes())
    if tamper == "cursor":
        raw["next"] = True
    elif tamper == "phase":
        raw["operations"][0]["phase"] = "execute"
    else:
        raw["operations"][0]["path"] = str(tmp_path.parent / "outside")
    result.journal.write_text(json.dumps(raw))
    with pytest.raises(LoadoutError, match="journal"):
        recover_migration(result.journal)
    assert (tmp_path / "loadout/config.toml").is_file()


def test_unmerged_index_is_rejected_before_preimages(tmp_path: Path) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    write(tmp_path, "app.txt", "content\n")
    oid = git(tmp_path, "hash-object", "-w", "app.txt").decode().strip()
    subprocess.run(
        ["git", "-C", str(tmp_path), "update-index", "--index-info"],
        input="".join(f"100644 {oid} {stage}\tapp.txt\n" for stage in (1, 2, 3)).encode(),
        check=True,
    )
    with pytest.raises(LoadoutError, match="unmerged"):
        prepare_migration(plan(tmp_path))
    assert len(git(tmp_path, "ls-files", "--unmerged").splitlines()) == 3
    assert (tmp_path / ".loadout-state").exists() is False


def test_signing_failure_preserves_requested_signing_and_original_files(tmp_path: Path) -> None:
    repository(tmp_path)
    target = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    signer = write(tmp_path, ".git/failing-signer", "#!/bin/sh\nexit 1\n", 0o755)
    git(tmp_path, "config", "commit.gpgsign", "true")
    git(tmp_path, "config", "user.signingkey", "fixture-only")
    git(tmp_path, "config", "gpg.program", str(signer))
    with pytest.raises(MigrationFailure, match="sign") as failure:
        apply_migration(prepare_migration(plan(tmp_path)))
    assert git(tmp_path, "config", "commit.gpgsign") == b"true\n"
    assert target.read_bytes() == b'{"model":"before"}'
    assert recover_migration(failure.value.journal).conflicts == ()


def test_interrupted_final_index_replacement_resumes_without_duplicate_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    real = migration_git.replace_index
    count = 0

    def interrupt(path: Path, before: bytes | None, after: bytes | None) -> None:
        nonlocal count
        real(path, before, after)
        count += 1
        if count == 2:
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        patch.setattr(migration_git, "replace_index", interrupt)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepare_migration(plan(tmp_path)))
    assert count == 2
    index = (tmp_path / ".git/index").read_bytes()
    resume_migration(failure.value.journal)
    assert (tmp_path / ".git/index").read_bytes() == index
    assert git(tmp_path, "rev-list", "--count", "HEAD") == b"1\n"
    assert cmd_check(tmp_path) == 0


@pytest.mark.parametrize("target", ["original", "canonical", "source"])
@pytest.mark.parametrize("initially_ignored", [False, True])
def test_resume_rechecks_privacy_after_failed_checkpoint(
    tmp_path: Path, target: str, initially_ignored: bool
) -> None:
    repository(tmp_path)
    original = write(tmp_path, "canonical/settings.json", '{"model":"before"}')
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude/settings.json").symlink_to(original)
    write(tmp_path, ".claude/commands/example.md", "public command\n")
    if initially_ignored:
        write(tmp_path, ".git/info/exclude", "canonical/settings.json\n")
    prepared = prepare_migration(plan(tmp_path))
    assert prepared.git is not None
    if target == "source":
        selected = next(
            w.path for w in prepared.plan.source_writes if w.content == b"public command\n"
        )
    else:
        selected = original if target == "canonical" else tmp_path / ".claude/settings.json"
    hook = write(tmp_path, ".git/hooks/pre-commit", "#!/bin/sh\nexit 1\n", 0o755)
    with pytest.raises(MigrationFailure) as failure:
        apply_migration(prepared)
    hook.unlink()
    write(
        tmp_path,
        ".git/info/exclude",
        "" if initially_ignored else selected.relative_to(tmp_path).as_posix() + "\n",
    )
    with pytest.raises(MigrationFailure, match="privacy"):
        resume_migration(failure.value.journal)
    assert migration_git.identity(tmp_path)[0] is None
    assert (tmp_path / "loadout").exists() is False


def interrupt_migration(
    prepared: migration_transaction.MigrationPreparation,
    phase: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    def fail(*args: object) -> None:
        raise KeyboardInterrupt()

    real = migration_git.replace_index
    count = 0

    def replace_index(path: Path, before: bytes | None, after: bytes | None) -> None:
        nonlocal count
        count += 1
        if count == 2 and phase == "before-index":
            raise KeyboardInterrupt()
        real(path, before, after)
        if count == 2 and phase == "after-index":
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        if phase in {"before-index", "after-index"}:
            patch.setattr(migration_git, "replace_index", replace_index)
        else:
            patch.setattr(
                migration_transaction,
                "_verify_after_checkpoint" if phase == "checkpoint" else "_finish",
                fail,
            )
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    journal = migration_journal.Journal.load(failure.value.journal)
    assert journal.status == ("stage-index" if phase.endswith("index") else "apply")
    return failure.value.journal


@pytest.mark.parametrize("phase", ["checkpoint", "finish", "before-index", "after-index"])
@pytest.mark.parametrize("target", ["original", "source", "canonical"])
def test_resume_rechecks_privacy_after_checkpoint_and_before_staging(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, phase: str, target: str
) -> None:
    repository(tmp_path)
    external = fake_home / "external"
    repository(external)
    canonical = external / "claude"
    settings = write(canonical, "settings.json", '{"model":"before"}')
    destination = fake_home / ".claude"
    destination.mkdir()
    (destination / "settings.json").symlink_to(settings)
    write(tmp_path, "claude/CLAUDE.md", "shared instructions\n")
    prepared = prepare_migration(
        plan(
            tmp_path,
            scope="global",
            mappings=(
                RootMapping(canonical, destination, ("claude",)),
                RootMapping(tmp_path / "claude", destination, ("claude",)),
            ),
        )
    )
    journal = interrupt_migration(prepared, phase, monkeypatch)
    head = git(tmp_path, "rev-parse", "HEAD")
    index = (tmp_path / ".git/index").read_bytes()
    if target == "canonical":
        write(external, ".git/info/exclude", "claude/settings.json\n")
    else:
        selected = (
            next(w.path for w in prepared.plan.source_writes if b'"before"' in w.content)
            if target == "source"
            else tmp_path / "claude/CLAUDE.md"
        )
        write(tmp_path, ".git/info/exclude", selected.relative_to(tmp_path).as_posix() + "\n")
    with pytest.raises(MigrationFailure, match="privacy"):
        resume_migration(journal)
    assert git(tmp_path, "rev-parse", "HEAD") == head
    assert (tmp_path / ".git/index").read_bytes() == index


@pytest.mark.parametrize("phase", ["checkpoint", "finish", "before-index", "after-index"])
@pytest.mark.parametrize("mutation", ["bytes", "mode"])
def test_resume_validates_unchanged_outputs_at_every_completion_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, mutation: str
) -> None:
    repository(tmp_path)
    output = write(tmp_path, ".claude/commands/example.md", "unchanged command\n")
    prepared = prepare_migration(plan(tmp_path))
    assert any(w.path == output for w in prepared.plan.generated_writes)
    assert all(o.path != output for o in prepared.operations)
    journal = interrupt_migration(prepared, phase, monkeypatch)
    if mutation == "bytes":
        output.write_text("concurrent command\n")
    else:
        output.chmod(0o755)
    index = (tmp_path / ".git/index").read_bytes()
    with pytest.raises(MigrationFailure, match="changed"):
        resume_migration(journal)
    assert (tmp_path / ".git/index").read_bytes() == index
    assert migration_journal.Journal.load(journal).status != "complete"


@pytest.mark.parametrize("scope", ["project", "global"])
@pytest.mark.parametrize("existing", [False, True])
def test_source_ancestor_alias_preserves_checkpoint_paths_and_link_entries(
    tmp_path: Path, fake_home: Path, scope: ArtifactScope, existing: bool
) -> None:
    physical = tmp_path / "physical"
    physical.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    if existing:
        repository(physical)
    else:
        write(
            fake_home,
            ".gitconfig",
            "[user]\nname = Migration Test\nemail = migration@example.invalid\n[commit]\ngpgsign = false\n",
        )
    original = write(physical, "canonical/settings.json", '{"model":"before"}')
    command = write(physical, "canonical/commands/example.md", "aliased command\n")
    link = physical / (".claude" if scope == "project" else "claude")
    link.symlink_to("canonical", target_is_directory=True)
    migration = plan(
        alias,
        scope=scope,
        mappings=(RootMapping(alias / "claude", fake_home / ".claude", ("claude",)),)
        if scope == "global"
        else (),
    )
    assert any(o.path == original and o.action == "retire" for o in migration.originals)
    prepared = prepare_migration(migration)
    assert prepared.git is not None
    assert prepared.git.baseline == tuple(
        sorted((link.name, "canonical/settings.json", "canonical/commands/example.md"))
    )
    assert prepared.additions == tuple(
        sorted(
            w.path.relative_to(alias).as_posix() for w in migration.source_writes if not w.private
        )
    )
    assert prepared.staged_ignore is not None and prepared.staged_ignore[0] == ".gitignore"
    apply_migration(prepared)
    assert git(physical, "show", "HEAD:canonical/settings.json") == b'{"model":"before"}'
    assert git(physical, "show", "HEAD:canonical/commands/example.md") == b"aliased command\n"
    assert git(physical, "ls-tree", "HEAD", link.name).startswith(b"120000 blob")
    assert original.exists() is False
    assert command.exists() is False
    assert set(prepared.additions).issubset(git(physical, "ls-files").decode().splitlines())
    assert cmd_check(physical if scope == "project" else physical / "loadout") == 0


def repository_with_partial_staging(outer: Path, physical: Path) -> tuple[Path, Path]:
    repository(outer)
    unrelated = write(outer, "unrelated.txt", "base\n")
    ignore = write(physical, ".gitignore", "base-ignore\n")
    git(outer, "add", ".")
    git(outer, "commit", "-qm", "base")
    unrelated.write_text("staged\n")
    ignore.write_text("base-ignore\nstaged-ignore\n")
    git(outer, "add", "unrelated.txt", ignore.relative_to(outer).as_posix())
    unrelated.write_text("worktree\n")
    ignore.write_text("base-ignore\nstaged-ignore\nworktree-ignore\n")
    return unrelated, ignore


@pytest.mark.parametrize("link_kind", ["none", "file", "directory"])
def test_nested_source_alias_preserves_enclosing_repository_index_and_privacy(
    tmp_path: Path, fake_home: Path, link_kind: str
) -> None:
    outer = tmp_path / "outer"
    physical = outer / "dotfiles"
    unrelated, ignore = repository_with_partial_staging(outer, physical)
    original = write(
        physical,
        "claude/settings.json" if link_kind == "none" else "canonical/settings.json",
        '{"model":"before"}',
    )
    link = physical / "claude"
    if link_kind == "directory":
        link.symlink_to("canonical", target_is_directory=True)
    elif link_kind == "file":
        link.mkdir()
        link = link / "settings.json"
        link.symlink_to("../canonical/settings.json")
    personal = write(physical, "claude/settings.local.json", '{"model":"personal"}')
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    migration = plan(
        alias,
        scope="global",
        mappings=(RootMapping(alias / "claude", fake_home / ".claude", ("claude",)),),
    )
    prepared = prepare_migration(migration)
    baseline = {original.relative_to(outer).as_posix()}
    if link_kind != "none":
        baseline.add(link.relative_to(outer).as_posix())
    assert prepared.git is not None
    assert prepared.git.root == outer
    assert prepared.git.baseline == tuple(sorted(baseline))
    apply_migration(prepared)
    assert set(git(outer, "ls-tree", "-r", "--name-only", "HEAD").decode().splitlines()) == (
        baseline | {"unrelated.txt", "dotfiles/.gitignore"}
    )
    assert git(outer, "show", "HEAD:" + original.relative_to(outer).as_posix()) == (
        b'{"model":"before"}'
    )
    if link_kind != "none":
        name = link.relative_to(outer).as_posix()
        assert git(outer, "ls-tree", "HEAD", name).startswith(b"120000 blob")
        assert git(outer, "show", "HEAD:" + name) == (
            b"canonical" if link_kind == "directory" else b"../canonical/settings.json"
        )
    public = {
        "dotfiles/" + w.path.relative_to(alias).as_posix(): w.content
        for w in migration.source_writes
        if not w.private
    }
    assert set(git(outer, "ls-files").decode().splitlines()) == (
        set(public) | {"unrelated.txt", "dotfiles/.gitignore"}
    )
    for name, content in public.items():
        assert git(outer, "show", ":" + name) == content
    assert git(outer, "show", "HEAD:unrelated.txt") == b"base\n"
    assert git(outer, "show", ":unrelated.txt") == b"staged\n"
    assert unrelated.read_bytes() == b"worktree\n"
    assert prepared.staged_ignore is not None
    assert prepared.staged_ignore[0] == "dotfiles/.gitignore"
    assert git(outer, "show", ":dotfiles/.gitignore") == prepared.staged_ignore[1]
    assert prepared.staged_ignore[1].splitlines()[:3] == [
        b"base-ignore",
        b"staged-ignore",
        b"/.loadout-state/",
    ]
    assert ignore.read_text().splitlines()[:3] == [
        "base-ignore",
        "staged-ignore",
        "worktree-ignore",
    ]
    private = next(w for w in migration.source_writes if w.private and w.content)
    assert private.path.parent.stat().st_mode & 0o777 == 0o700
    assert private.path.read_bytes() == private.content
    assert personal.read_bytes() == b'{"model":"personal"}'
    assert json.loads((fake_home / ".claude/settings.local.json").read_bytes()) == {
        "model": "personal"
    }
    assert alias.readlink() == physical
    assert cmd_check(physical / "loadout") == 0


@pytest.mark.parametrize("kind", ["global", "repository"])
@pytest.mark.parametrize("phase", ["checkpoint", "finish"])
def test_resume_rechecks_symlinked_exclude_file_contents(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, kind: str, phase: str
) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    target = write(fake_home, "ignore-rules", "")
    if kind == "global":
        link = fake_home / "global-ignore"
        git(tmp_path, "config", "core.excludesFile", str(link))
    else:
        link = tmp_path / ".git/info/exclude"
        link.unlink()
    link.symlink_to(target)
    journal = interrupt_migration(prepare_migration(plan(tmp_path)), phase, monkeypatch)
    target.write_text(".claude/settings.json\nfollowed-probe.txt\n")
    assert (
        git(tmp_path, "check-ignore", "--no-index", "--", "followed-probe.txt")
        == b"followed-probe.txt\n"
    )
    assert link.readlink() == target
    with pytest.raises(MigrationFailure, match="privacy"):
        resume_migration(journal)


def test_aliased_source_keeps_private_namespaces_protected(tmp_path: Path) -> None:
    physical = tmp_path / "physical"
    repository(physical)
    alias = tmp_path / "alias"
    alias.symlink_to(physical, target_is_directory=True)
    write(alias, ".claude/settings.local.json", '{"model":"personal"}')
    prepared = prepare_migration(plan(alias))
    apply_migration(prepared)
    private = next(w for w in prepared.plan.source_writes if w.private and w.content)
    assert private.path.parent.stat().st_mode & 0o777 == 0o700
    assert git(physical, "ls-files", "--", private.path.relative_to(alias).as_posix()) == b""


@pytest.mark.parametrize("action", ["apply", "resume", "pending"])
def test_unchanged_outputs_validate_before_retiring_originals(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    repository(tmp_path)
    original = write(tmp_path, "claude/settings.json", '{"model":"before"}')
    output = write(fake_home, ".claude/commands/example.md", "unchanged command\n")
    destination = fake_home / ".claude"
    prepared = prepare_migration(
        plan(
            tmp_path,
            scope="global",
            mappings=(RootMapping(original.parent, destination, ("claude",)),),
        )
    )
    assert original in prepared.plan.obsolete
    assert all(o.path != output for o in prepared.operations)
    if action == "apply":
        deploy = migration_transaction._deploy

        def change_after_deployment(
            journal: migration_journal.Journal, deployment: migration_transaction.DeploymentPlan
        ) -> None:
            deploy(journal, deployment)
            output.write_text("concurrent command\n")

        monkeypatch.setattr(migration_transaction, "_deploy", change_after_deployment)
        with pytest.raises(MigrationFailure, match="changed"):
            apply_migration(prepared)
    else:
        if action == "resume":
            journal_path = interrupt_migration(prepared, "checkpoint", monkeypatch)
        else:
            install = migration_journal.install

            def interrupt(path: Path, image: migration_journal.Image) -> None:
                install(path, image)
                if path == destination / "settings.json":
                    raise KeyboardInterrupt()

            with monkeypatch.context() as patch:
                patch.setattr(migration_journal, "install", interrupt)
                with pytest.raises(MigrationFailure) as failure:
                    apply_migration(prepared)
            journal_path = failure.value.journal
            assert migration_journal.Journal.load(journal_path).pending
        output.write_text("concurrent command\n")
        with pytest.raises(MigrationFailure, match="changed"):
            resume_migration(journal_path)
    assert original.read_bytes() == b'{"model":"before"}'
    assert output.read_text() == "concurrent command\n"


def test_final_index_resume_validates_dormant_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    prepared = prepare_migration(plan(tmp_path))
    absent = tmp_path / ".mcp.json"
    assert absent in prepared.plan.required_absences
    journal = interrupt_migration(prepared, "after-index", monkeypatch)
    absent.write_text("{}\n")
    with pytest.raises(MigrationFailure, match="dormant output"):
        resume_migration(journal)
