from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.written import read_written, written_state_path

pytestmark = pytest.mark.integration


def _sync(root: Path, *args: str) -> int:
    return loadout.main(["sync", "--root", str(root), *args])


def _kinds(root: Path, capsys) -> dict[str, str]:
    capsys.readouterr()
    loadout.main(["check", "--root", str(root), "--json"])
    drift = json.loads(capsys.readouterr().out)["drift"]
    return {str(Path(entry["path"]).relative_to(root)): entry["kind"] for entry in drift}


def _project(root: Path, harnesses: str = '"claude"') -> Path:
    source = root / "loadout"
    (source / "instructions").mkdir(parents=True)
    (source / "instructions" / "main.md").write_text("Shared rules.\n")
    (source / "config.toml").write_text(
        f'harnesses = [{harnesses}]\ninstructions = ["main"]\n', encoding="utf-8"
    )
    (source / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    return source


def _skill(source: Path, name: str) -> Path:
    folder = source / "skills" / name
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")
    (folder / "scripts" / "run.sh").write_text("echo hi\n")
    return folder


def _remove(path: Path) -> None:
    for child in sorted(path.rglob("*"), reverse=True):
        child.rmdir() if child.is_dir() else child.unlink()
    path.rmdir()


def test_a_removed_skill_s_folder_is_retired_with_its_gitignore_entry(
    tmp_path: Path, capsys
) -> None:
    source = _project(tmp_path)
    _skill(source, "keep")
    removed = _skill(source, "gone")
    assert _sync(tmp_path) == 0
    folder = tmp_path / ".claude" / "skills" / "gone"
    assert (folder / "scripts" / "run.sh").is_file()

    _remove(removed)
    capsys.readouterr()
    assert _sync(tmp_path) == 0

    out = capsys.readouterr().out
    assert "retired .claude/skills/gone/SKILL.md" in out
    assert "retired .claude/skills/gone/scripts/run.sh" in out
    assert "2 retired" in out
    assert "removed 1 entries from .gitignore" in out
    assert sorted(p.name for p in (tmp_path / ".claude" / "skills").iterdir()) == ["keep"]
    ignored = (tmp_path / ".gitignore").read_text().splitlines()
    assert "/.claude/skills/keep/" in ignored
    assert "/.claude/skills/gone/" not in ignored


def test_retired_destinations_leave_the_record_and_the_next_sync_is_up_to_date(
    tmp_path: Path, capsys
) -> None:
    source = _project(tmp_path)
    removed = _skill(source, "gone")
    assert _sync(tmp_path) == 0
    document = tmp_path / ".claude" / "skills" / "gone" / "SKILL.md"
    assert document in read_written(tmp_path)

    _remove(removed)
    assert _sync(tmp_path) == 0
    capsys.readouterr()

    assert document not in read_written(tmp_path)
    assert tmp_path / "CLAUDE.md" in read_written(tmp_path)
    assert _sync(tmp_path) == 0
    assert "Up to date." in capsys.readouterr().out


def test_a_file_recreated_where_one_was_retired_is_never_touched(tmp_path: Path) -> None:
    """Retired means forgotten: the path is no longer recorded, so whatever is put
    there afterwards is not loadout's and no later sync deletes it."""
    source = _project(tmp_path)
    removed = _skill(source, "gone")
    assert _sync(tmp_path) == 0
    _remove(removed)
    assert _sync(tmp_path) == 0
    mine = tmp_path / ".claude" / "skills" / "gone" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: gone\ndescription: mine now\n---\n")

    assert _sync(tmp_path) == 0

    assert mine.read_text() == "---\nname: gone\ndescription: mine now\n---\n"


def test_removing_a_harness_retires_its_instructions_and_rules(tmp_path: Path, capsys) -> None:
    source = _project(tmp_path, '"claude", "codex"')
    assert _sync(tmp_path) == 0
    assert (tmp_path / "AGENTS.md").is_file()
    assert (tmp_path / ".codex" / "rules" / "permissions.rules").is_file()

    (source / "config.toml").write_text('harnesses = ["claude"]\ninstructions = ["main"]\n')
    capsys.readouterr()
    assert _sync(tmp_path) == 0

    out = capsys.readouterr().out
    assert "retired AGENTS.md" in out
    assert "retired .codex/rules/permissions.rules" in out
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / ".codex" / "rules" / "permissions.rules").exists()
    assert (tmp_path / "CLAUDE.md").read_text().endswith("Shared rules.\n")
    ignored = (tmp_path / ".gitignore").read_text().splitlines()
    assert "/CLAUDE.md" in ignored
    assert "/AGENTS.md" not in ignored
    assert "/.codex/rules/permissions.rules" not in ignored


def test_removing_the_last_mcp_server_retires_mcp_json(tmp_path: Path, capsys) -> None:
    """The measured case: the server stayed listed and sync said "Up to date."."""
    source = _project(tmp_path)
    servers = source / "mcp.toml"
    servers.write_text('[docs]\ntransport = "stdio"\ncommand = "docs-server"\n')
    assert _sync(tmp_path) == 0
    assert "docs" in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]

    servers.unlink()
    capsys.readouterr()
    assert _sync(tmp_path) == 0

    out = capsys.readouterr().out
    assert "retired .mcp.json" in out
    assert "0 written" in out
    assert "1 retired" in out
    assert not (tmp_path / ".mcp.json").exists()


def test_a_skill_folder_holding_a_file_loadout_never_wrote_keeps_that_file(
    tmp_path: Path,
) -> None:
    source = _project(tmp_path)
    removed = _skill(source, "gone")
    assert _sync(tmp_path) == 0
    folder = tmp_path / ".claude" / "skills" / "gone"
    (folder / "scripts" / "notes.txt").write_text("my notes\n")

    _remove(removed)
    assert _sync(tmp_path) == 0

    assert (folder / "scripts" / "notes.txt").read_text() == "my notes\n"
    assert sorted(p.name for p in folder.rglob("*")) == ["notes.txt", "scripts"]


def test_a_hand_edited_orphan_is_refused_until_forced(tmp_path: Path, capsys) -> None:
    source = _project(tmp_path, '"claude", "codex"')
    assert _sync(tmp_path) == 0
    agents = tmp_path / "AGENTS.md"
    agents.write_text(agents.read_text() + "my own line\n")
    (source / "config.toml").write_text('harnesses = ["claude"]\ninstructions = ["main"]\n')
    capsys.readouterr()

    assert _sync(tmp_path) == 1

    err = capsys.readouterr().err
    assert "AGENTS.md was modified outside loadout and is no longer rendered" in err
    assert "-my own line" in err
    assert agents.read_text().endswith("my own line\n")
    assert (tmp_path / ".codex" / "rules" / "permissions.rules").is_file()

    assert _sync(tmp_path, "--force") == 0
    assert not agents.exists()
    assert not (tmp_path / ".codex" / "rules" / "permissions.rules").exists()


def test_without_a_record_nothing_is_retired(tmp_path: Path) -> None:
    """An output no record lists is not provably loadout's, however generated it looks."""
    source = _project(tmp_path, '"claude", "codex"')
    assert _sync(tmp_path) == 0
    claude = (tmp_path / "CLAUDE.md").read_text()
    written_state_path(tmp_path).unlink()
    (source / "config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')

    assert _sync(tmp_path) == 0

    assert (tmp_path / "CLAUDE.md").read_text() == claude
    assert claude.endswith("@AGENTS.md\n")


def test_check_reports_what_sync_would_retire(tmp_path: Path, capsys) -> None:
    source = _project(tmp_path, '"claude", "codex"')
    removed = _skill(source, "gone")
    assert _sync(tmp_path) == 0
    _remove(removed)
    agents = tmp_path / "AGENTS.md"
    (source / "config.toml").write_text('harnesses = ["claude"]\ninstructions = ["main"]\n')
    agents.write_text(agents.read_text() + "my own line\n")

    kinds = _kinds(tmp_path, capsys)
    assert loadout.main(["check", "--root", str(tmp_path)]) == 1

    assert kinds == {
        ".agents/skills/gone/SKILL.md": "orphaned",
        ".agents/skills/gone/scripts/run.sh": "orphaned",
        ".claude/skills/gone/SKILL.md": "orphaned",
        ".claude/skills/gone/scripts/run.sh": "orphaned",
        ".codex/rules/permissions.rules": "orphaned",
        "AGENTS.md": "hand-edited",
        "CLAUDE.md": "source-changed",
    }
    err = capsys.readouterr().err
    assert "DRIFT (orphaned): .codex/rules/permissions.rules" in err
    assert "5 orphaned: written by loadout but no longer rendered" in err
    assert "1 hand-edited" in err


def _global_source(root: Path, *, codex: bool = True) -> Path:
    root.mkdir()
    (root / "instructions").mkdir()
    (root / "instructions" / "me.md").write_text("# me\n")
    (root / "permissions.toml").write_text('[shell]\nallow = ["git status"]\n')
    (root / "mcp.toml").write_text('[docs]\ntransport = "stdio"\ncommand = "docs"\n')
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "mine"\npath = "."\n'
        'use = ["permissions", "mcp", "instructions"]\n\n'
        '[all]\ninstructions = ["me"]\n\n[claude]\npermissions = true\n'
        + ("\n[codex]\n" if codex else "")
    )
    return root


def test_retiring_a_shared_file_strips_only_loadout_s_keys(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _global_source(tmp_path / "source")
    assert _sync(root) == 0
    config = fake_home / ".codex" / "config.toml"
    config.write_text('model = "o3"\n\n' + config.read_text())
    assert "[mcp_servers.docs]" in config.read_text()

    _global_source_without_codex(root)
    capsys.readouterr()
    assert _sync(root) == 0

    assert f"retired {config}" in capsys.readouterr().out
    assert config.read_text().strip() == 'model = "o3"'
    assert not (fake_home / ".codex" / "AGENTS.md").exists()
    assert (fake_home / ".claude" / "CLAUDE.md").is_file()


def _global_source_without_codex(root: Path) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text().replace("\n[codex]\n", ""))


def test_another_tool_s_hook_survives_retiring_the_settings_it_lives_in(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """P-29's scenario the other way round: a hook another tool added to a settings
    file loadout no longer renders makes it a hand edit, so it is kept."""
    root = _global_source(tmp_path / "source")
    assert _sync(root) == 0
    settings = fake_home / ".claude" / "settings.json"
    document = json.loads(settings.read_text())
    hook = [{"hooks": [{"type": "command", "command": "compact.sh"}]}]
    document["hooks"] = {"PreCompact": hook}
    settings.write_text(json.dumps(document, indent=2) + "\n")
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text().replace("[claude]\npermissions = true\n", ""))
    capsys.readouterr()

    assert _sync(root) == 1

    assert "settings.json was modified outside loadout and is no longer rendered" in (
        capsys.readouterr().err
    )
    assert json.loads(settings.read_text())["hooks"]["PreCompact"] == hook
