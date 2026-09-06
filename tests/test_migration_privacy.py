from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock

import pytest

from loadout import migration_git, migration_transaction
from loadout.errors import LoadoutError
from loadout.migration_journal import Journal
from test_migration_transaction import git, repository, write


def test_privacy_policy_resolves_each_logical_parent_once(tmp_path, monkeypatch):
    repository(tmp_path)
    parents = (tmp_path / "claude/skills/test", tmp_path / "loadout/skills/test")
    for parent in parents:
        parent.mkdir(parents=True)
        (parent / ".gitignore").write_text("private.bin\n")
    parents += (tmp_path / "future/support",)
    expected = migration_git.privacy_policy(tmp_path, tuple(p / "000.bin" for p in parents))
    calls = []
    original = migration_git._privacy_repository

    def counted(parent, cache):
        calls.append(parent)
        return original(parent, cache)

    monkeypatch.setattr(migration_git, "_privacy_repository", counted)
    paths = tuple(parent / f"{number:03}.bin" for parent in parents for number in range(1000))
    assert migration_git.privacy_policy(tmp_path, paths) == expected
    assert calls == [*parents[:2], tmp_path]
    policy = dict(expected)
    assert {key for key in policy if key.startswith("repository:")} == {
        "repository:" + str(parent) for parent in parents
    }
    for parent in parents[:2]:
        assert policy["file:" + str(parent / ".gitignore")] == migration_git.privacy_fingerprint(
            parent / ".gitignore"
        )


def test_privacy_policy_refreshes_nested_repositories_and_logical_aliases(tmp_path):
    outer = tmp_path / "outer"
    repository(outer)
    nested = outer / "nested"
    source = write(nested, "assets/file.bin", "original\n")
    alias = tmp_path / "alias"
    alias.symlink_to(source.parent, target_is_directory=True)
    paths = (source, alias / source.name)
    before = dict(migration_git.privacy_policy(outer, paths))
    assert before["repository:" + str(source.parent)] == str(outer)
    assert before["repository:" + str(alias)] == str(outer)
    repository(nested)
    nested_policy = dict(migration_git.privacy_policy(outer, paths))
    assert nested_policy["repository:" + str(source.parent)] == str(nested)
    assert nested_policy["repository:" + str(alias)] == str(nested)
    assert "file:" + str(nested / ".git/info/exclude") in nested_policy
    other = tmp_path / "other"
    repository(other)
    write(other, "assets/file.bin", "other\n")
    alias.unlink()
    alias.symlink_to(other / "assets", target_is_directory=True)
    changed = dict(migration_git.privacy_policy(outer, paths))
    assert changed["repository:" + str(source.parent)] == str(nested)
    assert changed["repository:" + str(alias)] == str(other)
    assert "file:" + str(other / ".git/info/exclude") in changed


def test_privacy_policy_refreshes_ignore_content_configuration_and_symlinks(tmp_path):
    repository(tmp_path)
    source = write(tmp_path, "nested/source.bin", "original\n")
    excludes = write(tmp_path, "exclude-rules", "private.bin\n")
    git(tmp_path, "config", "core.excludesFile", str(excludes))
    git(tmp_path, "config", "core.ignoreCase", "false")
    paths = (source, source.with_name("other.bin"))
    before = dict(migration_git.privacy_policy(tmp_path, paths))
    for ignore in (source.parent / ".gitignore", tmp_path / ".git/info/exclude", excludes):
        ignore.write_text("new-private.bin\n")
        changed = dict(migration_git.privacy_policy(tmp_path, paths))
        key = "file:" + str(ignore)
        assert changed[key] == migration_git.privacy_fingerprint(ignore)
        assert changed[key] != before[key]
        before = changed
    replacement = write(tmp_path, "other-excludes", "different-private.bin\n")
    excludes.unlink()
    excludes.symlink_to(replacement)
    git(tmp_path, "config", "core.ignoreCase", "true")
    changed = dict(migration_git.privacy_policy(tmp_path, paths))
    assert changed["file:" + str(excludes)] == migration_git.privacy_fingerprint(excludes)
    assert changed["file:" + str(replacement)] == migration_git.privacy_fingerprint(replacement)
    assert changed["ignorecase:" + str(tmp_path)] == "true"


def test_privacy_policy_keeps_unborn_root_and_external_parent_distinct(tmp_path, fake_home):
    source = write(tmp_path, "nested/source.bin", "original\n")
    external = write(fake_home, "external/source.bin", "external\n")
    before = dict(migration_git.privacy_policy(tmp_path, (source, external)))
    assert before["repository:" + str(source.parent)] == str(tmp_path)
    assert before["repository:" + str(external.parent)] is None
    assert before["file:" + str(tmp_path / ".git/info/exclude")] is None
    repository(tmp_path)
    changed = dict(migration_git.privacy_policy(tmp_path, (source, external)))
    assert changed["repository:" + str(source.parent)] == str(tmp_path)
    assert changed["repository:" + str(external.parent)] is None
    assert changed["file:" + str(tmp_path / ".git/info/exclude")] == (
        migration_git.privacy_fingerprint(tmp_path / ".git/info/exclude")
    )


def guard_journal(root):
    head, symbolic = migration_git.identity(root)
    index_path = root / ".git/index"
    index = migration_git.index_bytes(index_path)
    paths = (root / "source.bin",)
    policy = migration_git.privacy_policy(root, paths)
    prepared = migration_git.GitPreparation(
        root, True, head, symbolic, index_path, index, (), (), (), (), (), paths, policy
    )
    return Journal(
        root / "unused-journal.json",
        (),
        {
            "git": migration_transaction._git_document(prepared),
            "baseline": head,
            "symbolic": symbolic,
            "index_path": str(index_path),
            "expected_index": migration_transaction._bytes(index),
            "privacy_policy": policy,
            "additions": ["source.bin"],
        },
    )


@pytest.mark.parametrize("state", ["unborn", "born", "detached"])
def test_parallel_git_guard_keeps_every_observation_and_ref_check(tmp_path, monkeypatch, state):
    repository(tmp_path)
    if state != "unborn":
        git(tmp_path, "commit", "--allow-empty", "-qm", "fixture")
    if state == "detached":
        git(tmp_path, "checkout", "--detach", "-q")
    journal = guard_journal(tmp_path)
    query = migration_git.git
    calls = Counter()
    lock = Lock()

    def counted(root, *args, **kwargs):
        with lock:
            calls[args] += 1
        return query(root, *args, **kwargs)

    monkeypatch.setattr(migration_git, "git", counted)
    migration_transaction._guard_git(journal)
    expected = calls.copy()
    assert sum(expected.values()) == 8
    calls.clear()
    with ThreadPoolExecutor(max_workers=4) as executor:
        migration_transaction._guard_git(journal, executor)
        assert calls == expected
        calls.clear()
        git(tmp_path, "symbolic-ref", "HEAD", "refs/heads/changed")
        with pytest.raises(LoadoutError, match="HEAD or symbolic ref changed"):
            migration_transaction._guard_git(journal, executor)
        assert calls == expected


def test_parallel_privacy_keeps_git_include_path_expansion_and_fresh_values(tmp_path, fake_home):
    repository(tmp_path)
    excludes = write(fake_home, "global-ignore", "private.bin\n")
    included = write(
        tmp_path, "included.conf", "[core]\nexcludesFile = ~/global-ignore\nignoreCase = true\n"
    )
    git(tmp_path, "config", "include.path", str(included))
    journal = guard_journal(tmp_path)
    expected = dict(journal.metadata["privacy_policy"])
    assert expected["excludes:" + str(tmp_path)] == str(excludes)
    assert expected["ignorecase:" + str(tmp_path)] == "true"
    with ThreadPoolExecutor(max_workers=4) as executor:
        migration_transaction._guard_git(journal, executor)
        included.write_text(included.read_text().replace("true", "false"))
        with pytest.raises(LoadoutError, match="privacy policy changed"):
            migration_transaction._guard_git(journal, executor)


def test_parallel_git_guard_checks_index_after_all_privacy_reads(tmp_path, monkeypatch):
    repository(tmp_path)
    journal = guard_journal(tmp_path)
    state = migration_transaction._guard_git_state
    privacy = migration_transaction._guard_privacy
    state_finished = Event()

    def checked(journal):
        state(journal)
        state_finished.set()

    def changed(journal, executor):
        privacy(journal, executor)
        assert state_finished.wait(5)
        (tmp_path / ".git/index").write_bytes(b"changed during privacy query\n")

    monkeypatch.setattr(migration_transaction, "_guard_git_state", checked)
    monkeypatch.setattr(migration_transaction, "_guard_privacy", changed)
    with (
        ThreadPoolExecutor(max_workers=4) as executor,
        pytest.raises(LoadoutError, match="Git index changed"),
    ):
        migration_transaction._guard_git(journal, executor)
    assert (tmp_path / ".git/index").read_bytes() == b"changed during privacy query\n"


@pytest.mark.parametrize("failure", ["none", "config", "git-path"])
def test_parallel_privacy_queries_all_finish_before_success_or_error(
    tmp_path, monkeypatch, failure
):
    repository(tmp_path)
    paths = (tmp_path / "source.bin",)
    expected = migration_git.privacy_policy(tmp_path, paths)
    query = migration_git.git
    simultaneous = Barrier(3, timeout=5)
    finished = []

    def observed(root, *args, **kwargs):
        selected = args[0] == "config" or args[-1] == "info/exclude"
        if selected:
            simultaneous.wait()
        try:
            if failure == "git-path" and args[-1] == "info/exclude":
                raise LoadoutError("fixture Git path error")
            if failure == "config" and args[-1] == "core.ignoreCase":
                return query(root, "-c", "core.ignoreCase=invalid", *args, **kwargs)
            return query(root, *args, **kwargs)
        finally:
            if selected:
                finished.append(args)

    monkeypatch.setattr(migration_git, "git", observed)
    with ThreadPoolExecutor(max_workers=4) as executor:
        if failure == "none":
            assert migration_git.privacy_policy(tmp_path, paths, executor=executor) == expected
        else:
            with pytest.raises(LoadoutError):
                migration_git.privacy_policy(tmp_path, paths, executor=executor)
        assert len(finished) == 3
