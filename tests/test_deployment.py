import subprocess
from pathlib import Path

import pytest

import loadout


def cli(root: Path, command: str, *args: str) -> int:
    return loadout.main([command, "--root", str(root), *args])


def _preset_project(root: Path, harnesses: str) -> Path:
    """A hand-authored project: the shape where a `harnesses`
    edit alone changes what sync generates. `permissions.toml` must exist even
    though only instructions are declared, or sync exits 3."""
    config = root / "loadout/config.toml"
    (root / "loadout").mkdir()
    (root / "loadout/permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
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


def _skill(directory: Path, name: str) -> None:
    (directory / name).mkdir(parents=True)
    (directory / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: d\n---\nbody\n", encoding="utf-8"
    )


def test_gitignore_names_each_skill_folder_loadout_writes(tmp_path: Path) -> None:
    """Loadout owns only the folders it writes, so the directory is never listed whole:
    that entry is what used to hide a hand-installed skill beside them."""
    _preset_project(tmp_path, '"claude"')
    _skill(tmp_path / "loadout" / "skills", "mine")

    assert cli(tmp_path, "sync") == 0

    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/skills/mine/" in ignored
    assert "/.claude/skills" not in ignored


@pytest.mark.integration
def test_a_skill_folder_with_glob_characters_is_ignored_by_its_literal_name(
    tmp_path: Path,
) -> None:
    """Review 3.3: sync escapes its lines the way init does, so `[1]` is not a glob."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _preset_project(tmp_path, '"claude"')
    _skill(tmp_path / "loadout" / "skills", "x[1] y")

    assert cli(tmp_path, "sync") == 0

    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/skills/x\\[1\\]\\ y/" in ignored
    check = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "--", ".claude/skills/x[1] y/SKILL.md"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.stdout == ".claude/skills/x[1] y/SKILL.md\n"


@pytest.mark.integration
def test_an_agent_file_with_glob_characters_is_ignored_by_its_literal_name(
    tmp_path: Path,
) -> None:
    """Review 2, batch 5 3.1: escaping was pinned only for skill folders."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    _preset_project(tmp_path, '"claude"')
    (tmp_path / "loadout/agents").mkdir(parents=True)
    (tmp_path / "loadout/agents/x[1].md").write_text(
        "---\ndescription: d\n---\nGo.\n", encoding="utf-8"
    )

    assert cli(tmp_path, "sync") == 0

    ignored = (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/agents/x\\[1\\].md" in ignored
    check = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "--", ".claude/agents/x[1].md"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.stdout == ".claude/agents/x[1].md\n"


def test_mcp_json_is_written_and_ignored_only_once_there_is_a_source(tmp_path: Path) -> None:
    _preset_project(tmp_path, '"claude"')
    hand_written = '{"mcpServers": {"cli": {"command": "x"}}}\n'
    (tmp_path / ".mcp.json").write_text(hand_written, encoding="utf-8")

    assert cli(tmp_path, "sync") == 0
    assert (tmp_path / ".mcp.json").read_text(encoding="utf-8") == hand_written
    assert "/.mcp.json" not in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()

    (tmp_path / "loadout" / "mcp.toml").write_text(
        '[docs]\ntransport = "stdio"\ncommand = "docs-mcp"\n', encoding="utf-8"
    )
    (tmp_path / ".mcp.json").unlink()
    assert cli(tmp_path, "sync") == 0
    assert "docs-mcp" in (tmp_path / ".mcp.json").read_text(encoding="utf-8")
    assert "/.mcp.json" in (tmp_path / ".gitignore").read_text(encoding="utf-8").splitlines()


def _outputs(root: Path) -> dict[Path, int]:
    return {
        path: path.stat().st_mtime_ns
        for path in root.rglob("*")
        if path.is_file() and "loadout" not in path.relative_to(root).parts[:1]
    }


def test_a_sync_with_nothing_to_change_rewrites_nothing(tmp_path: Path, capsys) -> None:
    _preset_project(tmp_path, '"claude", "codex"')
    assert cli(tmp_path, "sync") == 0
    before = _outputs(tmp_path)
    capsys.readouterr()

    assert cli(tmp_path, "sync") == 0

    assert capsys.readouterr().out.splitlines() == ["Up to date."]
    assert len(before) > 3
    assert _outputs(tmp_path) == before


def test_a_one_line_source_change_writes_exactly_one_file(tmp_path: Path, capsys) -> None:
    _preset_project(tmp_path, '"claude"')
    assert cli(tmp_path, "sync") == 0
    fragment = tmp_path / "loadout" / "instructions" / "main.md"
    fragment.write_text(fragment.read_text(encoding="utf-8") + "One more rule.\n")
    capsys.readouterr()

    assert cli(tmp_path, "sync") == 0

    lines = capsys.readouterr().out.splitlines()
    assert [line for line in lines if line.startswith("wrote ")] == ["wrote CLAUDE.md"]
    assert lines[-1] == "1 written, 1 unchanged"


def test_a_skill_still_rendered_keeps_its_entry_when_one_of_its_files_retires(
    tmp_path: Path,
) -> None:
    """Review 2, batch 5 3.3: the retired file maps to its skill's folder entry, which
    the skill still being rendered needs."""
    _preset_project(tmp_path, '"claude"')
    skills = tmp_path / "loadout" / "skills"
    _skill(skills, "review")
    (skills / "review" / "helper.md").write_text("help\n", encoding="utf-8")
    assert cli(tmp_path, "sync") == 0
    (skills / "review" / "helper.md").unlink()

    assert cli(tmp_path, "sync") == 0

    assert not (tmp_path / ".claude/skills/review/helper.md").exists()
    assert "/.claude/skills/review/" in (tmp_path / ".gitignore").read_text().splitlines()


def test_an_orphan_that_is_no_longer_a_file_keeps_its_entry(tmp_path: Path) -> None:
    """Review 2, batch 5 3.3: retiring leaves what is not a file, so its entry stays."""
    _preset_project(tmp_path, '"claude"')
    assert cli(tmp_path, "sync") == 0
    (tmp_path / "CLAUDE.md").unlink()
    (tmp_path / "CLAUDE.md").mkdir()
    (tmp_path / "CLAUDE.md" / "notes.md").write_text("mine\n", encoding="utf-8")
    (tmp_path / "loadout/config.toml").write_text('harnesses = ["claude"]\n', encoding="utf-8")

    assert cli(tmp_path, "sync") == 0

    assert (tmp_path / "CLAUDE.md" / "notes.md").read_text() == "mine\n"
    assert "/CLAUDE.md" in (tmp_path / ".gitignore").read_text().splitlines()
