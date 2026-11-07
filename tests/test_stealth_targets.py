from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.harnesses import GLOBAL_PRESET
from loadout.stealth_manifest import StealthSettings
from loadout.stealth_targets import (
    _HOME_TEMPLATES,
    HarnessHomes,
    SkippedTarget,
    Target,
    expand_patterns,
    expand_targets,
    harness_homes,
    probe,
    recorded_fate,
    refuse_unsafe_outputs,
)

skip_as_root = pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 folder")


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "commit", "-q", "--allow-empty", "-m", "base")
    return path.resolve()


@contextmanager
def _unreadable(folder: Path) -> Iterator[Path]:
    folder.chmod(0)
    try:
        yield folder
    finally:
        folder.chmod(0o755)


@pytest.fixture
def ceiling(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def _homes(home: Path) -> HarnessHomes:
    return HarnessHomes(home=home.resolve(), homes=(home.resolve() / ".claude",))


def test_expand_patterns_keeps_resolved_folders_once(tmp_path: Path) -> None:
    repos = tmp_path / "repos"
    (repos / "app1").mkdir(parents=True)
    (repos / "app2").mkdir()
    (repos / "app3.txt").write_text("file\n")
    (repos / "app4").symlink_to(repos / "app1")

    matches, unlisted = expand_patterns(["~/repos/app*"], tmp_path)

    assert matches == {"~/repos/app*": ((repos / "app1").resolve(), (repos / "app2").resolve())}
    assert unlisted == ()


@skip_as_root
def test_a_pattern_under_an_unlistable_parent_is_unlisted(tmp_path: Path) -> None:
    (tmp_path / "locked" / "app1").mkdir(parents=True)
    with _unreadable(tmp_path / "locked"):
        expansion = expand_targets(
            StealthSettings(targets=("~/locked/app*", "~/missing/app*")),
            _homes(tmp_path),
            tmp_path / "stealth",
        )

    assert expansion.unlisted == ("~/locked/app*",)
    assert expansion.unlisted_under == ((tmp_path / "locked").resolve(),)


def test_a_present_root_is_present(tmp_path: Path) -> None:
    assert recorded_fate(tmp_path, os.stat(tmp_path).st_dev) == "present"


def test_a_deleted_root_on_the_recorded_device_is_gone(tmp_path: Path) -> None:
    root = tmp_path / "tree" / "app1"
    root.mkdir(parents=True)
    device = os.stat(root).st_dev
    shutil.rmtree(tmp_path / "tree")

    assert recorded_fate(root, device) == "gone"


def test_a_missing_root_on_another_device_is_unreachable(tmp_path: Path) -> None:
    root = tmp_path / "volume" / "app1"

    assert recorded_fate(root, os.stat(tmp_path).st_dev + 1) == "unreachable"


@skip_as_root
def test_a_root_that_cannot_be_stated_is_unreachable(tmp_path: Path) -> None:
    root = tmp_path / "locked" / "app1"
    root.mkdir(parents=True)
    device = os.stat(root).st_dev
    with _unreadable(tmp_path / "locked"):
        assert recorded_fate(root, device) == "unreachable"


def _target(root: Path) -> Target:
    return Target(root=root, common_dir=root / ".git", device=0)


def test_refuse_unsafe_outputs_refuses_a_path_in_a_nested_repository(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / ".claude" / ".git").mkdir(parents=True)

    with pytest.raises(LoadoutError, match=r"\.claude is a repository of its own"):
        refuse_unsafe_outputs(
            _target(root),
            [root / "CLAUDE.local.md", root / ".claude" / "settings.local.json"],
            [],
        )


def test_refuse_unsafe_outputs_refuses_a_link_on_the_way(tmp_path: Path) -> None:
    root = tmp_path.resolve() / "repo"
    (root / "elsewhere").mkdir(parents=True)
    (root / ".claude").symlink_to(root / "elsewhere")

    with pytest.raises(LoadoutError, match=r"through the link .*\.claude"):
        refuse_unsafe_outputs(_target(root), [root / ".claude" / "settings.local.json"], [])


def test_refuse_unsafe_outputs_passes_real_folders_inside_the_root(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    (root / ".claude").mkdir()
    (root / ".git").mkdir()

    refuse_unsafe_outputs(
        _target(root),
        [root / ".claude" / "settings.local.json", root / "CLAUDE.local.md"],
        [],
    )


def test_every_harness_home_template_prefixes_a_destination_in_the_global_preset() -> None:
    for harness, template in _HOME_TEMPLATES.items():
        destinations = [output.destination for output in GLOBAL_PRESET[harness].values()]
        assert any(destination.startswith(template) for destination in destinations), harness


def test_harness_homes_follow_the_harness_variables(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    homes = harness_homes()

    assert homes.home == fake_home.resolve()
    assert homes.homes == (
        fake_home.resolve() / ".claude",
        (tmp_path / "codex").resolve(),
        fake_home.resolve() / ".factory",
        fake_home.resolve() / ".config" / "opencode",
        fake_home.resolve() / ".pi" / "agent",
    )


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_checkouts_are_targets_and_a_plain_folder_is_a_note(tmp_path: Path) -> None:
    app1 = _repo(tmp_path / "repos" / "app1")
    app2 = _repo(tmp_path / "repos" / "app2")
    (tmp_path / "repos" / "app3").mkdir()
    app3 = (tmp_path / "repos" / "app3").resolve()

    expansion = expand_targets(
        StealthSettings(targets=(str(tmp_path / "repos" / "app*"),)),
        harness_homes(),
        tmp_path / "stealth",
    )

    assert expansion.targets == (
        Target(app1, app1 / ".git", os.stat(app1).st_dev),
        Target(app2, app2 / ".git", os.stat(app2).st_dev),
    )
    assert expansion.skipped == (SkippedTarget(app3, "not-a-checkout", "not a git checkout"),)
    assert expansion.matched == frozenset({app1, app2, app3})


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_only_probes_the_named_folder_and_still_lists_every_match(tmp_path: Path) -> None:
    app1 = _repo(tmp_path / "repos" / "app1")
    app2 = _repo(tmp_path / "repos" / "app2")

    expansion = expand_targets(
        StealthSettings(targets=(str(tmp_path / "repos" / "app*"),)),
        harness_homes(),
        tmp_path / "stealth",
        only=tmp_path / "repos" / "app2",
    )

    assert [target.root for target in expansion.targets] == [app2]
    assert expansion.matched == frozenset({app1, app2})


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_a_submodule_is_skipped_and_its_parent_is_a_target(tmp_path: Path) -> None:
    origin = _repo(tmp_path / "origin")
    parent = _repo(tmp_path / "parent")
    _git(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(origin), "sub")

    homes = harness_homes()
    sub = probe(parent / "sub", homes, tmp_path / "stealth")
    main = probe(parent, homes, tmp_path / "stealth")

    assert isinstance(sub, SkippedTarget)
    assert (sub.kind, sub.failure) == ("submodule", False)
    assert isinstance(main, Target)
    assert main.root == parent


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_a_linked_worktree_is_skipped_and_its_main_checkout_is_a_target(tmp_path: Path) -> None:
    main = _repo(tmp_path / "main")
    _git(main, "worktree", "add", "-q", str(tmp_path / "linked"))

    homes = harness_homes()
    linked = probe(tmp_path / "linked", homes, tmp_path / "stealth")
    checkout = probe(main, homes, tmp_path / "stealth")

    assert isinstance(linked, SkippedTarget)
    assert (linked.kind, linked.failure) == ("linked-worktree", False)
    assert isinstance(checkout, Target)
    assert checkout.common_dir == main / ".git"


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_a_git_file_naming_a_missing_folder_is_a_git_failure(tmp_path: Path) -> None:
    folder = tmp_path / "broken"
    folder.mkdir()
    (folder / ".git").write_text(f"gitdir: {tmp_path / 'missing'}\n")

    result = probe(folder, harness_homes(), tmp_path / "stealth")

    assert isinstance(result, SkippedTarget)
    assert (result.kind, result.failure) == ("git-failed", True)
    assert "git cannot read its repository" in result.reason


@skip_as_root
@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_an_unreadable_git_folder_is_a_git_failure(tmp_path: Path) -> None:
    folder = _repo(tmp_path / "locked")
    with _unreadable(folder / ".git"):
        result = probe(folder, harness_homes(), tmp_path / "stealth")

    assert isinstance(result, SkippedTarget)
    assert (result.kind, result.failure) == ("git-failed", True)
    assert "git cannot read its repository" in result.reason


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_without_git_on_path_a_probe_is_a_git_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = _repo(tmp_path / "app1")
    homes = harness_homes()
    monkeypatch.setenv("PATH", "")

    result = probe(folder, homes, tmp_path / "stealth")

    assert isinstance(result, SkippedTarget)
    assert (result.kind, result.failure) == ("git-failed", True)
    assert "git is not on PATH" in result.reason


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_home_a_harness_home_and_the_stealth_folder_are_unsafe(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _repo(fake_home)
    team = _repo(tmp_path / "team")
    stealth = _repo(tmp_path / "stealth")
    inner = _repo(tmp_path / "stealth" / "inner")
    monkeypatch.setenv("CODEX_HOME", str(team / ".codex"))
    homes = harness_homes()

    results = {folder: probe(folder, homes, stealth) for folder in (home, team, stealth, inner)}

    assert {
        folder: (result.kind, result.failure, result.reason)
        for folder, result in results.items()
        if isinstance(result, SkippedTarget)
    } == {
        home: ("unsafe", True, "a target cannot be the home folder"),
        team: ("unsafe", True, f"a target cannot hold the harness home {team / '.codex'}"),
        stealth: ("unsafe", True, f"a target cannot hold the stealth folder {stealth}"),
        inner: ("unsafe", True, f"a target cannot be inside the stealth folder {stealth}"),
    }


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_a_folder_with_a_git_entry_that_is_not_its_own_top_is_unsafe(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    (repo / "packages" / "web" / ".git").mkdir(parents=True)

    result = probe(repo / "packages" / "web", harness_homes(), tmp_path / "stealth")

    assert isinstance(result, SkippedTarget)
    assert (result.kind, result.failure) == ("unsafe", True)
    assert result.reason == f"inside the checkout {repo}; a target is the top of a checkout"


@pytest.mark.integration
def test_a_plain_folder_in_an_enclosing_checkout_is_a_note_and_a_nested_clone_is_a_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enclosing = _repo(tmp_path / "h")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    app1 = _repo(enclosing / "repos" / "app1")
    (enclosing / "repos" / "plain").mkdir()
    plain = (enclosing / "repos" / "plain").resolve()

    expansion = expand_targets(
        StealthSettings(targets=(str(enclosing / "repos" / "*"),)),
        harness_homes(),
        tmp_path / "stealth",
    )

    assert [target.root for target in expansion.targets] == [app1]
    assert expansion.skipped == (SkippedTarget(plain, "not-a-checkout", "not a git checkout"),)


@pytest.mark.integration
def test_a_bare_repository_matched_by_a_pattern_is_a_not_a_checkout_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    app = _repo(tmp_path / "repos" / "app")
    bare = tmp_path / "repos" / "mirror.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare")

    expansion = expand_targets(
        StealthSettings(targets=(str(tmp_path / "repos" / "*"),)),
        harness_homes(),
        tmp_path / "stealth",
    )

    assert [target.root for target in expansion.targets] == [app]
    assert expansion.skipped == (
        SkippedTarget(bare.resolve(), "not-a-checkout", "a bare repository has no working tree"),
    )


def _case_insensitive(folder: Path) -> bool:
    (folder / "probe").mkdir()
    return (folder / "PROBE").exists()


@pytest.mark.integration
@pytest.mark.usefixtures("ceiling")
def test_a_pattern_spelled_in_another_case_still_yields_the_target(tmp_path: Path) -> None:
    if not _case_insensitive(tmp_path):
        pytest.skip("the temporary file system is case-sensitive")
    app1 = _repo(tmp_path / "repos" / "app1")

    expansion = expand_targets(
        StealthSettings(
            targets=(str(tmp_path / "Repos" / "app*"), str(tmp_path / "repos" / "app*"))
        ),
        harness_homes(),
        tmp_path / "stealth",
    )

    assert [target.root for target in expansion.targets] == [app1]
    assert expansion.skipped == ()
    assert len(expansion.matched) == 1
