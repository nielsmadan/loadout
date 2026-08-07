from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import scaffold
from loadout.errors import LoadoutError
from loadout.scaffold import add_harness, init_project


def git_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def test_creates_the_source_files(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    d = root / "loadout"
    assert (d / "config.toml").read_text(encoding="utf-8") == 'harnesses = ["claude"]\n'
    assert (d / "permissions.toml").is_file()
    assert (d / "permissions.local.toml").is_file()


def test_committed_source_carries_a_header_and_personal_is_empty(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    d = root / "loadout"
    assert (d / "permissions.toml").read_text(encoding="utf-8").startswith("#")
    assert (d / "permissions.local.toml").read_text(encoding="utf-8") == ""


def test_gitignores_the_personal_source_and_every_output(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude", "pi"))
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "loadout/permissions.local.toml" in ignored
    assert ".claude/settings.json" in ignored
    assert ".aiconf/mcp-permissions.json" in ignored
    assert ".pi/extensions/pi-permission-system/config.json" in ignored


def test_gitignore_is_idempotent(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    first = (root / ".gitignore").read_text(encoding="utf-8")
    init_project(root, ("claude",))
    assert (root / ".gitignore").read_text(encoding="utf-8") == first


def test_existing_gitignore_content_is_preserved(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    (root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    init_project(root, ("claude",))
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignored[0] == "*.pyc"
    assert "loadout/permissions.local.toml" in ignored


def test_warns_but_succeeds_when_a_tracked_instruction_file_exists(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    (root / "CLAUDE.md").write_text("# project rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "CLAUDE.md"], check=True)
    actions = init_project(root, ("claude",))
    assert (root / "loadout" / "config.toml").is_file()
    assert any("CLAUDE.md" in a for a in actions)


def test_an_untracked_instruction_file_does_not_block_init(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    (root / "CLAUDE.md").write_text("# scratch\n", encoding="utf-8")
    init_project(root, ("claude",))
    assert (root / "loadout" / "config.toml").is_file()


@pytest.mark.parametrize("name", ["AGENTS.md", "GEMINI.md"])
def test_warns_but_succeeds_when_any_tracked_instruction_file_exists(
    tmp_path: Path, name: str
) -> None:
    root = git_repo(tmp_path)
    (root / name).write_text("# project rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", name], check=True)
    actions = init_project(root, ("claude",))
    assert (root / "loadout" / "config.toml").is_file()
    assert any(name in a for a in actions)


@pytest.mark.parametrize("name", ["AGENTS.md", "GEMINI.md"])
def test_an_untracked_instruction_file_of_any_kind_does_not_block_init(
    tmp_path: Path, name: str
) -> None:
    root = git_repo(tmp_path)
    (root / name).write_text("# scratch\n", encoding="utf-8")
    init_project(root, ("claude",))
    assert (root / "loadout" / "config.toml").is_file()


def test_missing_git_executable_raises_a_loadout_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = git_repo(tmp_path)
    (root / "CLAUDE.md").write_text("# scratch\n", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError("git")

    monkeypatch.setattr(scaffold.subprocess, "run", fake_run)
    with pytest.raises(LoadoutError, match="git"):
        init_project(root, ("claude",))


def test_reinit_recreates_a_missing_source_file(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    permissions_path = root / "loadout" / "permissions.toml"
    permissions_path.unlink()
    assert not permissions_path.is_file()

    actions = init_project(root, ("claude",))

    assert permissions_path.is_file()
    assert permissions_path.read_text(encoding="utf-8").startswith("#")
    assert any("permissions.toml" in a for a in actions)


def test_reinit_with_same_harness_set_in_different_order_is_a_noop(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude", "pi"))
    config_path = root / "loadout" / "config.toml"
    original = config_path.read_text(encoding="utf-8")

    init_project(root, ("pi", "claude"))

    assert config_path.read_text(encoding="utf-8") == original


def test_refuses_to_overwrite_an_existing_project(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    with pytest.raises(LoadoutError, match="already"):
        init_project(root, ("codex",))


def test_unknown_harness_is_rejected(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    with pytest.raises(LoadoutError, match="emacs"):
        init_project(root, ("emacs",))


def test_duplicate_harness_is_rejected(tmp_path: Path) -> None:
    """init_project used to bypass load_project_config's duplicate check by
    constructing ProjectConfig directly — a `loadout init --harness claude
    --harness claude` would succeed and then `loadout sync` would fail."""
    root = git_repo(tmp_path)
    with pytest.raises(LoadoutError, match="duplicate"):
        init_project(root, ("claude", "claude"))
    assert not (root / "loadout" / "config.toml").exists()


def test_gitignore_additions_are_deduped_against_themselves(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    scaffold._append_gitignore(root, ["a", "b", "a"])
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ignored.count("a") == 1
    assert ignored == ["a", "b"]


def test_reports_every_action_taken(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    actions = init_project(root, ("claude",))
    assert any("loadout/config.toml" in a for a in actions)
    assert any(".gitignore" in a for a in actions)


def test_adds_the_harness_to_the_config(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    add_harness(root, "codex")
    assert (root / "loadout" / "config.toml").read_text(encoding="utf-8") == (
        'harnesses = ["claude", "codex"]\n'
    )


def test_adds_the_new_harness_outputs_to_gitignore(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    add_harness(root, "codex")
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".codex/rules/aiconf.rules" in ignored


def test_adding_an_existing_harness_is_an_error(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    with pytest.raises(LoadoutError, match="already enabled"):
        add_harness(root, "claude")


def test_adding_an_unknown_harness_is_an_error(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    init_project(root, ("claude",))
    with pytest.raises(LoadoutError, match="emacs"):
        add_harness(root, "emacs")


def test_adding_before_init_is_an_error(tmp_path: Path) -> None:
    root = git_repo(tmp_path)
    with pytest.raises(LoadoutError, match="not found"):
        add_harness(root, "codex")
