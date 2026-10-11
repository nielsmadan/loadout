from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from loadout import migration_git, migration_journal, migration_transaction
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
from loadout.output import ArtifactScope

pytestmark = pytest.mark.migration_integration


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


def interrupted_completion(prepared: migration_transaction.MigrationPreparation) -> Path:
    save = migration_journal.Journal.save

    def interrupt(journal: migration_journal.Journal) -> None:
        if journal.status == "complete":
            raise KeyboardInterrupt()
        save(journal)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(migration_journal.Journal, "save", interrupt)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    return failure.value.journal


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
    recovery = recover_migration(failure.value.journal)
    assert recovery.conflicts == ()
    assert recovery.journal is None
    assert not failure.value.journal.parent.exists()


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
    assert result.journal is None
    assert list((tmp_path / ".loadout-state/migrations").iterdir()) == []


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


@pytest.mark.parametrize("private", [False, True])
def test_partially_staged_adopted_files_require_resolution_even_when_private(
    tmp_path: Path, private: bool
) -> None:
    repository(tmp_path)
    name = ".claude/skills/s/notes.local.md" if private else ".claude/settings.json"
    write(tmp_path, ".claude/skills/s/SKILL.md", "# s\n")
    target = write(tmp_path, name, '{"model":"base"}')
    git(tmp_path, "add", ".claude")
    git(tmp_path, "commit", "-qm", "base")
    target.write_text('{"model":"staged"}')
    git(tmp_path, "add", name)
    target.write_text('{"model":"worktree"}')
    with pytest.raises(LoadoutError, match="partially staged adopted"):
        prepare_migration(plan(tmp_path))
    assert git(tmp_path, "show", ":" + name) == b'{"model":"staged"}'


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


def test_recovery_after_final_staging_keeps_baseline_and_restores_original_entries(
    tmp_path: Path,
) -> None:
    repository(tmp_path)
    settings = write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    journal = interrupted_completion(prepare_migration(plan(tmp_path)))
    baseline = git(tmp_path, "rev-parse", "HEAD")
    recovery = recover_migration(journal)
    assert recovery.conflicts == ()
    assert settings.read_bytes() == b'{"model":"before"}'
    assert git(tmp_path, "rev-parse", "HEAD") == baseline
    assert git(tmp_path, "diff", "--cached", "--name-only") == b""
    assert recovery.journal is None
    assert not journal.parent.exists()


def test_recovery_after_user_commit_preserves_committed_source_and_index(tmp_path: Path) -> None:
    repository(tmp_path)
    write(tmp_path, ".claude/settings.json", '{"model":"before"}')
    journal = interrupted_completion(prepare_migration(plan(tmp_path)))
    git(tmp_path, "commit", "-qm", "user adopted migration")
    head = git(tmp_path, "rev-parse", "HEAD")
    index = (tmp_path / ".git/index").read_bytes()
    source = (tmp_path / "loadout/config.toml").read_bytes()
    recovery = recover_migration(journal)
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
    path = interrupted_completion(prepare_migration(plan(tmp_path)))
    if tamper == "cursor":
        raw = json.loads(path.read_bytes())
        raw["next"] = True
        path.write_text(json.dumps(raw))
    else:
        journal = migration_journal.Journal.load(path)
        operation = journal.operations[0]
        operation = (
            replace(operation, phase="execute")
            if tamper == "phase"
            else replace(operation, path=tmp_path.parent / "outside")
        )
        journal.operations = (operation, *journal.operations[1:])
        journal.save()
    with pytest.raises(LoadoutError, match="journal"):
        recover_migration(path)
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
