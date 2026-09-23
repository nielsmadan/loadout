from pathlib import Path

import loadout


def cli(root: Path, command: str, *args: str) -> int:
    return loadout.main([command, "--root", str(root), *args])


def _preset_project(root: Path, harnesses: str) -> Path:
    """A hand-authored project: the shape where a `harnesses`
    edit alone changes what sync generates. `permissions.toml` must exist even
    though only instructions are declared, or sync exits 3 (P-21)."""
    config = root / "loadout/config.toml"
    (root / "loadout").mkdir()
    (root / "loadout/permissions.toml").write_text("", encoding="utf-8")
    (root / "loadout/instructions").mkdir()
    (root / "loadout/instructions/main.md").write_text("Shared rules.\n", encoding="utf-8")
    config.write_text(
        f'harnesses = [{harnesses}]\ninstructions = ["main"]\n',
        encoding="utf-8",
    )
    return config


def test_sync_extends_gitignore_when_a_harness_is_added_to_the_list(tmp_path: Path) -> None:
    config = _preset_project(tmp_path, '"claude"')
    assert cli(tmp_path, "sync") == 0
    assert (tmp_path / "CLAUDE.md").is_file()
    before = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/settings.json" in before
    assert "/.codex/rules/permissions.rules" not in before

    config.write_text(
        'harnesses = ["claude", "codex"]\ninstructions = ["main"]\n',
        encoding="utf-8",
    )
    assert cli(tmp_path, "sync") == 0
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / ".codex/rules/permissions.rules").is_file()
    after = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.codex/rules/permissions.rules" in after

    assert cli(tmp_path, "sync") == 0
    repeated = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert repeated.count("/.codex/rules/permissions.rules") == 1
    assert "/loadout/instructions/main.md" not in repeated


def test_sync_leaves_gitignore_entries_of_removed_harnesses_in_place(tmp_path: Path) -> None:
    """Entries are appended, never retired: loadout has no deletion story for its
    own outputs, so sync does not invent one for their ignore entries either."""
    config = _preset_project(tmp_path, '"claude", "codex"')
    assert cli(tmp_path, "sync") == 0
    config.write_text('harnesses = ["claude"]\ninstructions = ["main"]\n', encoding="utf-8")
    assert cli(tmp_path, "sync") == 0
    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.codex/rules/permissions.rules" in ignored


def test_sync_maintains_gitignore_without_a_git_repository(tmp_path: Path) -> None:
    """`init project` neither requires nor creates a repository, so a plain
    directory is a supported project root and its `.gitignore` must be
    maintained there too."""
    _preset_project(tmp_path, '"claude"')
    assert not (tmp_path / ".git").exists()
    assert cli(tmp_path, "sync") == 0
    assert (tmp_path / "CLAUDE.md").is_file()
    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/settings.json" in ignored
