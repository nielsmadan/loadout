"""A file loadout did not write, in a folder a harness reads whole, is flagged.

Codex reads every file in `~/.codex/rules/`, and each harness reads every folder in
its skills directory, so a file there that loadout did not write takes effect beside
the source. `check` fails on it and `sync` names it; the /loadout skill ports it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import loadout
from registration import register

pytestmark = pytest.mark.integration

SOURCE = '[[source]]\nname = "test"\npath = "."\n\n[claude]\n\n[codex]\n'
ADVICE = (
    "not written by loadout, in a folder the harness reads whole; port it into the source "
    "with /loadout, then delete it"
)


def _global_source(root: Path, skill: str = "review") -> Path:
    (root / "skills" / skill).mkdir(parents=True)
    (root / "loadout.toml").write_text(SOURCE)
    (root / "permissions.toml").write_text('[shell]\nallow = ["git status"]\n')
    (root / "skills" / skill / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: {skill}.\n---\nBody.\n"
    )
    return root


def _synced(root: Path, capsys) -> Path:
    assert loadout.main(["sync", "--root", str(root)]) == 0
    capsys.readouterr()
    return root


def _check_json(root: Path, capsys) -> tuple[int, dict[str, str]]:
    code = loadout.main(["check", "--root", str(root), "--json"])
    result = json.loads(capsys.readouterr().out)
    return code, {entry["path"]: entry["kind"] for entry in result["drift"]}


def test_check_flags_an_approvals_file_codex_wrote_beside_the_rendered_rules(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    approvals = fake_home / ".codex/rules/default.rules"
    approvals.write_text('prefix_rule(pattern=["ls"], decision="allow")\n')

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(approvals): "unmanaged"}


def test_check_explains_an_unmanaged_file_and_shows_its_content(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    approvals = fake_home / ".codex/rules/default.rules"
    approvals.write_text('prefix_rule(pattern=["ls"], decision="allow")\n')

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"DRIFT (unmanaged): {approvals}" in err
    assert '-prefix_rule(pattern=["ls"], decision="allow")' in err
    assert f"1 unmanaged: {ADVICE}." in err


def test_sync_names_an_unmanaged_file_and_leaves_it_in_place(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    approvals = fake_home / ".codex/rules/default.rules"
    approvals.write_text('prefix_rule(pattern=["ls"], decision="allow")\n')

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert f"WARNING: {approvals} is {ADVICE}" in capsys.readouterr().err
    assert approvals.read_text() == 'prefix_rule(pattern=["ls"], decision="allow")\n'


def test_a_hand_installed_skill_is_flagged_as_one_folder(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    foreign = fake_home / ".claude/skills/foreign"
    (foreign / "scripts").mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: foreign\n---\nForeign.\n")
    (foreign / "scripts/run.sh").write_text("true\n")

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(foreign): "unmanaged"}


def test_a_file_added_inside_a_skill_loadout_wrote_is_flagged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    extra = fake_home / ".claude/skills/review/notes.md"
    extra.write_text("Notes.\n")

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(extra): "unmanaged"}


def test_a_skill_another_loadout_root_wrote_is_not_flagged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    first = _synced(register(_global_source(tmp_path / "first", "review")), capsys)
    second = _synced(register(_global_source(tmp_path / "second", "deploy")), capsys)
    assert (fake_home / ".claude/skills/deploy/SKILL.md").is_file()

    assert _check_json(register(first), capsys) == (0, {})
    assert _check_json(register(second), capsys) == (0, {})


def test_os_and_harness_state_in_a_managed_folder_is_not_flagged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    (fake_home / ".codex/rules/.DS_Store").write_bytes(b"\0")
    system = fake_home / ".codex/skills/.system/imagegen"
    system.mkdir(parents=True)
    (system / "SKILL.md").write_text("---\nname: imagegen\n---\nCodex's own.\n")

    assert _check_json(root, capsys) == (0, {})


def test_a_project_skill_folder_loadout_did_not_write_is_flagged(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    _synced(root, capsys)
    foreign = root / ".claude/skills/foreign"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("---\nname: foreign\n---\nForeign.\n")

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(foreign): "unmanaged"}


def test_an_agent_loadout_did_not_write_in_a_global_agents_folder_is_flagged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 5.6: each harness reads every file in its agents folder."""
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    foreign = fake_home / ".claude/agents/foreign.md"
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("---\nname: foreign\ndescription: f\n---\nForeign.\n")

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(foreign): "unmanaged"}


def test_a_rules_file_loadout_did_not_write_in_a_project_codex_folder_is_flagged(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    _synced(root, capsys)
    approvals = root / ".codex/rules/default.rules"
    approvals.write_text('prefix_rule(pattern=["ls"], decision="allow")\n')

    code, kinds = _check_json(root, capsys)

    assert code == 1
    assert kinds == {str(approvals): "unmanaged"}


def test_a_runtime_file_in_a_managed_folder_is_harness_state_not_unmanaged(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _synced(register(_global_source(tmp_path / "source")), capsys)
    (fake_home / ".codex/rules/auth.json").write_text('{"token": "x"}\n')

    assert _check_json(root, capsys) == (0, {})


BUCKET = "41a07887-org_dabe1acb-account"
MISSION_DROIDS = ("scrutiny-feature-reviewer.md", "user-testing-flow-validator.md")


def _account_skills(skills: Path) -> Path:
    """Claude Code's sync of the claude.ai account's skills, as 2.1.307 lays it out."""
    synced = skills / "synced"
    (synced / BUCKET / ".staging").mkdir(parents=True)
    (synced / f".bucket-{BUCKET}").write_text("")
    (synced / BUCKET / "manifest.json").write_text("{}\n")
    (synced / BUCKET / "pdf").mkdir()
    (synced / BUCKET / "pdf/SKILL.md").write_text("---\nname: pdf\n---\nAccount skill.\n")
    return synced


def _mission_droids(droids: Path) -> tuple[Path, ...]:
    droids.mkdir(parents=True, exist_ok=True)
    for name in MISSION_DROIDS:
        (droids / name).write_text(
            f"---\nname: {Path(name).stem}\n---\nUsed only within missions.\n"
        )
    return tuple(droids / name for name in MISSION_DROIDS)


@pytest.mark.parametrize("relocated", [False, True])
def test_entries_the_harnesses_write_themselves_are_not_flagged_in_their_own_folder(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys, relocated: bool
) -> None:
    claude, factory = fake_home / ".claude", fake_home
    if relocated:
        claude, factory = tmp_path / "claude-config", tmp_path / "factory-home"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
        monkeypatch.setenv("FACTORY_HOME_OVERRIDE", str(factory))
    source = _global_source(tmp_path / "source")
    (source / "loadout.toml").write_text(SOURCE + "\n[droid]\n")
    _synced(register(source), capsys)
    _account_skills(claude / "skills")
    _mission_droids(factory / ".factory/droids")
    foreign = claude / "skills/foreign"
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("---\nname: foreign\n---\nForeign.\n")

    assert loadout.main(["check", "--global", "--json"]) == 1
    drift = json.loads(capsys.readouterr().out)["drift"]
    assert {entry["path"]: entry["kind"] for entry in drift} == {str(foreign): "unmanaged"}

    shutil.rmtree(foreign)
    assert loadout.main(["check", "--global"]) == 0


def test_the_names_harnesses_write_themselves_are_flagged_anywhere_else(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_source(tmp_path / "source")
    (source / "loadout.toml").write_text(SOURCE + "\n[droid]\n")
    _synced(register(source), capsys)
    elsewhere = (
        _account_skills(fake_home / ".codex/skills"),
        _account_skills(fake_home / ".factory/skills"),
        _account_skills(fake_home / ".claude/skills/review"),
        *_mission_droids(fake_home / ".claude/agents"),
    )

    code, kinds = _check_json(source, capsys)

    assert code == 1
    assert kinds == {str(path): "unmanaged" for path in elsewhere}


def test_a_harness_variable_that_does_not_resolve_leaves_the_other_folders_scanned(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    source = _global_source(tmp_path / "source")
    (source / "loadout.toml").write_text('[[source]]\nname = "test"\npath = "."\n\n[codex]\n')
    _synced(register(source), capsys)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "relative/claude")
    approvals = fake_home / ".codex/rules/default.rules"
    approvals.write_text('prefix_rule(pattern=["ls"], decision="allow")\n')

    assert _check_json(source, capsys) == (1, {str(approvals): "unmanaged"})
