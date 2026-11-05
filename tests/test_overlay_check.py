from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from fixture_root import OVERLAY_FIXTURES
from loadout import git_runner, overlay_check, overlay_sync
from loadout.exclude import PERSONAL_UNITS
from loadout.files import Drift, DriftKind
from loadout.overlay_check import (
    Machine,
    OverlayCheck,
    TargetView,
    check_overlay,
    global_ignore_hint,
    report_check,
    target_notes,
)
from loadout.overlay_plan import TargetPlan
from loadout.overlay_record import (
    TargetRecord,
    empty_record,
    overlay_record_path,
    with_target,
    write_overlay_record,
)
from loadout.overlay_registry import RegisteredOverlay
from loadout.overlay_sync import OverlayRun, PreparedRun, shown, sync_overlay
from loadout.overlay_targets import Expansion
from loadout.written import text_entry

SETTINGS = ".claude/settings.local.json"
EMPTY_EXPANSION = Expansion(
    targets=(), skipped=(), matched=frozenset(), unlisted=(), unlisted_under=()
)


def _entry(root: Path) -> TargetRecord:
    return TargetRecord(
        common_dir=root / ".git",
        device=0,
        block=None,
        files={},
        documents={},
        leaves={},
        pending={},
    )


def _plan(root: Path, **fields: Any) -> TargetPlan:
    base: dict[str, Any] = {
        "root": root,
        "whole": False,
        "writes": {},
        "deletes": (),
        "block_before": (),
        "block_after": (),
        "ahead": _entry(root),
        "record": _entry(root),
        "wanted_leaves": {},
        "retiring_leaves": {},
        "refusals": (),
        "drift": (),
        "carried": (),
        "notes": (),
        "code": 0,
    }
    return TargetPlan(**{**base, **fields})


def _machine(tmp_path: Path, **fields: Any) -> Machine:
    base: dict[str, Any] = {
        "codex_file": tmp_path / "codex" / "config.toml",
        "codex": {},
        "pi_trust_file": tmp_path / "pi" / "trust.json",
        "pi_trust": {},
        "opencode_race": False,
    }
    return Machine(**{**base, **fields})


def _view(root: Path, outputs: tuple[str, ...] = (), **fields: Any) -> TargetView:
    base: dict[str, Any] = {
        "root": root,
        "outputs": outputs,
        "codex_config": None,
        "overlay_opencode": {},
        "team_opencode": {},
        "pi_mcp_edited": False,
        "branches": {},
        "carried": (),
        "render_notes": (),
    }
    return TargetView(**{**base, **fields})


def _fabricated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, plans: tuple[TargetPlan, ...]
) -> OverlayCheck:
    monkeypatch.setattr(
        overlay_check,
        "prepare",
        lambda run: PreparedRun(
            record=empty_record(run.overlay.folder, "fabricated", tmp_path),
            expansion=EMPTY_EXPANSION,
            plans=plans,
            errors=(),
            shared=(),
        ),
    )
    monkeypatch.setattr(overlay_check, "tracked_units", lambda root, units: frozenset())
    monkeypatch.setattr(overlay_check, "branches_tracking", lambda root, units: {})
    overlay = RegisteredOverlay("fabricated", tmp_path / "no-such-overlay")
    return check_overlay(overlay, None)


@pytest.mark.parametrize(
    "kind",
    ["missing", "hand-edited", "source-changed", "orphaned", "owned-key", "owned-key-removed"],
)
def test_each_drift_kind_is_reported_with_its_path_and_a_diff(
    kind: DriftKind, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "CLAUDE.local.md"
    check = OverlayCheck(
        "app", (Drift(path=path, actual="old\n", expected="new\n", kind=kind),), (), (), 1
    )

    report_check(check)

    err = capsys.readouterr().err.splitlines()
    assert err[0] == f"overlay app: DRIFT ({kind}): {path}"
    assert "-old" in err
    assert "+new" in err


def test_a_clean_check_says_so_and_prints_its_notes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_check(OverlayCheck("app", (), ("~/x: a note",), (), 0))

    assert capsys.readouterr().out.splitlines() == [
        "note: overlay app: ~/x: a note",
        "overlay app: up to date",
    ]


def test_a_carried_approval_is_one_note_and_never_moves_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    plan = _plan(root, carried=("permissions.allow: Bash(npm run x)",))

    check = _fabricated(monkeypatch, tmp_path, (plan,))

    assert check.code == 0
    assert check.drift == ()
    assert check.notes == (
        (
            f"{shown(root)}: saved by the harness, not in the overlay source: "
            "permissions.allow: Bash(npm run x); add it to the overlay's permissions.toml or delete it"
        ),
    )


def test_a_hand_edited_pi_mcp_file_says_where_the_change_belongs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    edited = Drift(path=root / ".pi" / "mcp.json", actual="a", expected="b", kind="hand-edited")

    check = _fabricated(monkeypatch, tmp_path, (_plan(root, drift=(edited,), code=1),))

    assert check.code == 1
    assert check.notes == (f"{shown(root)}: {overlay_check.PI_MCP_NOTE}",)


def test_a_failed_target_is_an_error_with_its_code_and_a_guard_refusal_is_only_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = tmp_path / "repo"
    tracked = overlay_check.Refusal("tracked", 3, "the team tracks opencode.json", root)
    guard = overlay_check.Refusal("guard", 1, "edited outside loadout", root)

    check = _fabricated(monkeypatch, tmp_path, (_plan(root, refusals=(tracked, guard), code=3),))

    assert check.errors == ((root, "refused: the team tracks opencode.json"),)
    assert check.code == 3


def test_an_untrusted_codex_project_gets_the_trust_note_and_a_trusted_one_none(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    view = _view(root, (".codex/config.toml",))
    machine = _machine(tmp_path)

    untrusted = target_notes(view, machine, ("codex",))
    own = target_notes(
        view,
        _machine(tmp_path, codex={"projects": {str(root): {"trust_level": "trusted"}}}),
        ("codex",),
    )
    ancestor = target_notes(
        view,
        _machine(tmp_path, codex={"projects": {str(tmp_path): {"trust_level": "trusted"}}}),
        ("codex",),
    )

    assert untrusted == (
        (
            f"{shown(root)}: Codex ignores the project's .codex/ until the project is trusted; add "
            f'[projects."{root}"] with trust_level = "trusted" to {machine.codex_file}'
        ),
    )
    assert own == ()
    assert ancestor == ()


def test_a_user_level_developer_instructions_is_reported_as_displaced(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    machine = _machine(
        tmp_path,
        codex={
            "developer_instructions": "mine",
            "projects": {str(root): {"trust_level": "trusted"}},
        },
    )
    view = _view(root, (".codex/config.toml",), codex_config='developer_instructions = "team"\n')

    assert target_notes(view, machine, ("codex",)) == (
        (
            f"{shown(root)}: .codex/config.toml sets developer_instructions, which replaces the one "
            f"in {machine.codex_file} for this project"
        ),
    )


def test_pi_trust_is_decided_by_the_nearest_saved_entry(tmp_path: Path) -> None:
    root = (tmp_path / "repo").resolve()
    root.mkdir()
    view = _view(root, (".pi/mcp.json",))

    none = target_notes(view, _machine(tmp_path), ("pi",))
    trusted = target_notes(
        view, _machine(tmp_path, pi_trust={str(tmp_path.resolve()): True}), ("pi",)
    )
    refused = target_notes(
        view,
        _machine(tmp_path, pi_trust={str(tmp_path.resolve()): True, str(root): False}),
        ("pi",),
    )

    assert len(none) == 1
    assert "holds no decision for this folder" in none[0]
    assert trusted == ()
    assert len(refused) == 1
    assert "records this folder as not trusted" in refused[0]


def test_a_team_opencode_file_is_named_for_each_server_and_rule_it_overrides(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    view = _view(
        root,
        ("opencode.json",),
        overlay_opencode={
            "mcp": {"docs": {"type": "remote"}, "mine": {}},
            "permission": {"bash": {"git push": "deny", "ls": "allow"}, "edit": "ask"},
        },
        team_opencode={
            "mcp": {"docs": {"type": "local"}},
            "permission": {"bash": {"git push": "allow", "ls": "allow"}, "edit": "allow"},
        },
    )

    notes = target_notes(view, _machine(tmp_path), ("opencode",))

    assert [note.split(": ", 1)[1].split(" ")[0:6] for note in notes] == [
        [".opencode/opencode.json", "is", "tracked", "and", "also", "defines"],
        [".opencode/opencode.json", "is", "tracked", "and", "sets", "a"],
        [".opencode/opencode.json", "is", "tracked", "and", "sets", "a"],
    ]
    assert "the MCP server docs" in notes[0]
    assert "permission for bash.git push" in notes[1]
    assert "permission for edit" in notes[2]


def test_skills_twice_needs_opencode_claude_skills_and_the_race(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    view = _view(root, (".claude/skills/probe/SKILL.md",))

    racing = target_notes(view, _machine(tmp_path, opencode_race=True), ("claude", "opencode"))
    undeclared = target_notes(view, _machine(tmp_path, opencode_race=True), ("claude",))
    disabled = target_notes(view, _machine(tmp_path), ("claude", "opencode"))

    assert len(racing) == 1
    assert "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1" in racing[0]
    assert undeclared == ()
    assert disabled == ()


def test_a_shared_skill_also_rendered_for_opencode_is_named(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    view = _view(root, (".agents/skills/probe/SKILL.md", ".opencode/skills/probe/SKILL.md"))

    notes = target_notes(view, _machine(tmp_path), ("codex", "opencode"))

    assert len(notes) == 1
    assert "the skill probe is in .agents/skills and in .opencode/skills" in notes[0]


def test_a_branch_tracking_an_output_is_named_with_the_cost_of_checking_it_out(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    view = _view(
        root, ("opencode.json",), branches={"opencode.json": ("team",), "CLAUDE.local.md": ()}
    )

    assert target_notes(view, _machine(tmp_path), ()) == (
        (
            f"{shown(root)}: opencode.json is tracked on the local branch team; checking it out "
            "replaces the overlay's copy and drops hand edits to it, so make the edits in the "
            "overlay source"
        ),
    )


def test_the_global_ignore_hint_is_absent_when_every_unit_is_covered(tmp_path: Path) -> None:
    file = tmp_path / "ignore"

    covered = global_ignore_hint("unasked", PERSONAL_UNITS, frozenset(PERSONAL_UNITS), file)
    partly = global_ignore_hint("unasked", PERSONAL_UNITS, frozenset({"CLAUDE.local.md"}), file)
    answered = global_ignore_hint("declined", PERSONAL_UNITS, frozenset(), file)

    assert covered == ()
    assert answered == ()
    assert len(partly) == 1
    assert partly[0].startswith(".claude/settings.local.json, .factory/settings.local.json are")


def test_pi_trust_ignores_numbers_and_unrelated_outputs(tmp_path: Path) -> None:
    root = (tmp_path / "repo").resolve()
    root.mkdir()

    numeric = target_notes(
        _view(root, (".pi/mcp.json",)), _machine(tmp_path, pi_trust={str(root): 1}), ("pi",)
    )
    unrelated = target_notes(_view(root, (".pi/notes.md",)), _machine(tmp_path), ("pi",))
    skills = target_notes(_view(root, (".pi/skills/x/SKILL.md",)), _machine(tmp_path), ("pi",))

    assert len(numeric) == 1
    assert "holds no decision for this folder" in numeric[0]
    assert unrelated == ()
    assert len(skills) == 1


# --- integration -----------------------------------------------------------------------------


@pytest.fixture
def isolated_git(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "test-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _overlay(home: Path, *names: str) -> tuple[RegisteredOverlay, list[Path]]:
    folder = home / "overlays" / "fixture"
    shutil.copytree(OVERLAY_FIXTURES, folder)
    roots = []
    for name in names:
        root = home / f"overlay-fixture-{name}"
        root.mkdir()
        _git(root, "init", "-q", "-b", "main")
        (root / "AGENTS.md").write_text("Team instructions.\n", encoding="utf-8")
        _git(root, "add", "AGENTS.md")
        _git(root, "commit", "-q", "-m", "team")
        roots.append(root.resolve())
    return RegisteredOverlay("fixture", folder.resolve()), roots


def _sync(overlay: RegisteredOverlay, *, force: bool = False) -> None:
    outcome = sync_overlay(OverlayRun(overlay, None, force, False))
    assert outcome.code == 0, outcome.errors


def _kinds(check: OverlayCheck, root: Path) -> dict[str, str]:
    return {
        item.path.relative_to(root).as_posix(): item.kind
        for item in check.drift
        if item.path.is_relative_to(root)
    }


def _leaves(check: OverlayCheck) -> dict[str, str]:
    return {
        str(item.path).split("#", 1)[1]: item.kind for item in check.drift if "#" in str(item.path)
    }


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_check_tells_each_kind_of_drift_apart_and_never_writes(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)

    clean = check_overlay(overlay, None)
    assert (clean.drift, clean.errors, clean.code) == ((), (), 0)

    (root / "CLAUDE.local.md").unlink()
    missing = check_overlay(overlay, None)
    assert _kinds(missing, root) == {"CLAUDE.local.md": "missing"}
    assert missing.code == 1
    assert not (root / "CLAUDE.local.md").exists()
    _sync(overlay)

    (root / ".factory" / "AGENTS.md").write_text("by hand\n", encoding="utf-8")
    edited = check_overlay(overlay, None)
    assert _kinds(edited, root) == {".factory/AGENTS.md": "hand-edited"}
    assert (root / ".factory" / "AGENTS.md").read_text(encoding="utf-8") == "by hand\n"
    _sync(overlay, force=True)

    personal = overlay.folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")
    changed = check_overlay(overlay, None)
    assert _kinds(changed, root)["CLAUDE.local.md"] == "source-changed"
    assert set(_kinds(changed, root).values()) == {"source-changed"}
    _sync(overlay)

    shutil.rmtree(overlay.folder / "skills" / "probe")
    dropped = check_overlay(overlay, None)
    assert set(_kinds(dropped, root).values()) == {"orphaned"}
    assert ".claude/skills/probe/SKILL.md" in _kinds(dropped, root)
    _sync(overlay)

    claude_json = fake_home / ".claude.json"
    document = json.loads(claude_json.read_text(encoding="utf-8"))
    del document["projects"][str(root)]["mcpServers"]["docs"]
    claude_json.write_text(json.dumps(document), encoding="utf-8")
    removed = check_overlay(overlay, None)
    assert _leaves(removed) == {f'projects["{root}"].mcpServers.docs': "owned-key-removed"}
    assert removed.code == 1
    assert (
        "docs"
        not in json.loads(claude_json.read_text(encoding="utf-8"))["projects"][str(root)][
            "mcpServers"
        ]
    )


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_team_skill_is_not_unmanaged_and_an_untracked_one_is(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    team = root / ".claude" / "skills" / "team-skill"
    team.mkdir(parents=True)
    (team / "SKILL.md").write_text("---\nname: team-skill\ndescription: x\n---\n", encoding="utf-8")
    _git(root, "add", "-f", ".claude/skills/team-skill/SKILL.md")
    _git(root, "commit", "-q", "-m", "team skill")
    (team / "stray.md").write_text("untracked\n", encoding="utf-8")
    mine = root / ".claude" / "skills" / "mine"
    mine.mkdir()
    (mine / "SKILL.md").write_text("---\nname: mine\ndescription: x\n---\n", encoding="utf-8")
    _sync(overlay)

    check = check_overlay(overlay, None)

    assert _kinds(check, root) == {
        ".claude/skills/mine": "unmanaged",
        ".claude/skills/team-skill": "unmanaged",
    }
    assert check.code == 1
    assert (team / "stray.md").read_text(encoding="utf-8") == "untracked\n"


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_fully_tracked_team_skill_is_not_unmanaged(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    team = root / ".claude" / "skills" / "team-skill"
    team.mkdir(parents=True)
    (team / "SKILL.md").write_text("---\nname: team-skill\ndescription: x\n---\n", encoding="utf-8")
    _git(root, "add", "-f", ".claude/skills/team-skill/SKILL.md")
    _git(root, "commit", "-q", "-m", "team skill")
    _sync(overlay)

    check = check_overlay(overlay, None)

    assert (check.drift, check.errors, check.code) == ((), (), 0)


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_skills_folder_the_overlay_writes_nothing_into_is_not_scanned(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    shutil.rmtree(overlay.folder / "skills")
    mine = root / ".claude" / "skills" / "mine"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: mine\ndescription: x\n---\n", encoding="utf-8")
    _sync(overlay)

    check = check_overlay(overlay, None)

    assert check.code == 0
    assert (root / "CLAUDE.local.md").is_file()
    assert (mine / "SKILL.md").is_file()
    assert [
        kind for path, kind in _kinds(check, root).items() if path.startswith(".claude/skills")
    ] == []


def _snapshot(home: Path, root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(home).as_posix(): path.read_bytes()
        for path in sorted(home.rglob("*"))
        if path.is_file() and not path.is_relative_to(root / ".git" / "objects")
    }


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_check_leaves_every_file_under_home_byte_identical(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    (root / "CLAUDE.local.md").unlink()
    (root / ".factory" / "AGENTS.md").write_text("by hand\n", encoding="utf-8")
    stray = root / ".claude" / "skills" / "stray"
    stray.mkdir()
    (stray / "SKILL.md").write_text("---\nname: stray\ndescription: x\n---\n", encoding="utf-8")
    before = _snapshot(fake_home, root)
    assert any(name.endswith(".git/info/exclude") for name in before)
    assert any(name.endswith(".claude.json") for name in before)

    check = check_overlay(overlay, None)

    assert check.code == 1
    assert _kinds(check, root) == {
        "CLAUDE.local.md": "missing",
        ".factory/AGENTS.md": "hand-edited",
        ".claude/skills/stray": "unmanaged",
    }
    assert _snapshot(fake_home, root) == before


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_the_global_ignore_hint_follows_what_the_users_ignore_file_covers(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)

    uncovered = check_overlay(overlay, None)
    ignore = fake_home / "global-ignore"
    ignore.write_text("\n".join(PERSONAL_UNITS) + "\n", encoding="utf-8")
    _git(root, "config", "--global", "core.excludesFile", str(ignore))
    covered = check_overlay(overlay, None)

    hints = [note for note in uncovered.notes if "are ignored only per repository" in note]
    assert len(hints) == 1
    assert hints[0].startswith(
        "CLAUDE.local.md, .claude/settings.local.json, .factory/settings.local.json"
    )
    assert [note for note in covered.notes if "are ignored only per repository" in note] == []


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_check_reports_the_overlay_folder_file_as_sync_judges_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, _ = _overlay(fake_home, "a")
    _sync(overlay)
    shared = overlay.folder / ".loadout" / "opencode-instructions.md"
    workflow = overlay.folder / "instructions" / "workflow.md"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "\nMore.\n", encoding="utf-8")

    changed = check_overlay(overlay, None)
    assert [(item.path, item.kind) for item in changed.drift if item.path == shared] == [
        (shared, "source-changed")
    ]
    capsys.readouterr()
    synced = sync_overlay(OverlayRun(overlay, None, False, False))
    overlay_sync.report(synced)
    assert f"overlay fixture: {shown(overlay.folder)}: wrote .loadout/opencode-instructions.md" in (
        capsys.readouterr().out.splitlines()
    )

    shared.write_text(shared.read_text(encoding="utf-8") + "by hand\n", encoding="utf-8")
    edited = check_overlay(overlay, None)
    assert [(item.path, item.kind) for item in edited.drift] == [(shared, "hand-edited")]
    assert edited.code == 1
    assert sync_overlay(OverlayRun(overlay, None, False, False)).code == 1


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_only_the_overlay_writing_opencode_json_is_told_a_team_file_outranks_it(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    team = root / ".opencode" / "opencode.json"
    team.parent.mkdir()
    team.write_text('{"mcp": {"docs": {"type": "local", "command": ["t"]}}}\n', encoding="utf-8")
    _git(root, "add", ".opencode/opencode.json")
    _git(root, "commit", "-q", "-m", "team opencode")
    quiet = fake_home / "overlays" / "quiet"
    quiet.mkdir()
    (quiet / "loadout.toml").write_text(
        '[overlay]\ntargets = ["~/overlay-fixture*"]\n\n[[source]]\nname = "quiet"\n'
        'path = "."\n\n[pi]\n',
        encoding="utf-8",
    )

    writer = check_overlay(overlay, None)
    other = check_overlay(RegisteredOverlay("quiet", quiet.resolve()), None)

    outranked = (
        f"{shown(root)}: .opencode/opencode.json is tracked and also defines the MCP server docs"
    )
    assert [note for note in writer.notes if note.startswith(outranked)] == [
        f"{outranked}; it outranks the overlay's opencode.json, so its definition wins"
    ]
    assert [note for note in other.notes if ".opencode/opencode.json" in note] == []
    assert other.code == 0


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_branch_tracking_an_output_is_named_in_the_notes(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    rendered = (root / "opencode.json").read_text(encoding="utf-8")
    _git(root, "checkout", "-q", "-b", "team")
    _git(root, "add", "-f", "opencode.json")
    _git(root, "commit", "-q", "-m", "team opencode")
    _git(root, "checkout", "-q", "main")
    _sync(overlay)
    assert (root / "opencode.json").read_text(encoding="utf-8") == rendered

    check = check_overlay(overlay, None)

    assert [note for note in check.notes if "local branch" in note] == [
        (
            f"{shown(root)}: opencode.json is tracked on the local branch team; checking it out "
            "replaces the overlay's copy and drops hand edits to it, so make the edits in the "
            "overlay source"
        ),
    ]
    assert check.code == 0


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_foreign_unreadable_record_is_reported_by_file_and_nothing_is_written(
    fake_home: Path,
) -> None:
    overlay, _ = _overlay(fake_home, "a")
    _sync(overlay)
    foreign = overlay_record_path(fake_home / "overlays" / "elsewhere")
    foreign.write_text("{not json", encoding="utf-8")
    before = foreign.read_text(encoding="utf-8")

    check = check_overlay(overlay, None)

    assert check.code == 3
    assert any(str(foreign) in message for _, message in check.errors)
    assert foreign.read_text(encoding="utf-8") == before


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
@pytest.mark.skipif(os.geteuid() == 0, reason="root lists a chmod 000 folder")
def test_a_pattern_whose_parent_cannot_be_listed_is_a_note_in_check_and_in_sync(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, _ = _overlay(fake_home, "a")
    blocked = fake_home / "blocked"
    blocked.mkdir()
    manifest = overlay.folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'targets = ["~/overlay-fixture*"]',
            'targets = ["~/overlay-fixture*", "~/blocked/app*"]',
        ),
        encoding="utf-8",
    )
    blocked.chmod(0o000)
    try:
        check = check_overlay(overlay, None)
        outcome = sync_overlay(OverlayRun(overlay, None, False, False))
        overlay_sync.report(outcome)
    finally:
        blocked.chmod(0o755)

    note = "the pattern ~/blocked/app* cannot be listed, so its targets are neither written nor retired"
    assert note in check.notes
    assert f"note: overlay fixture: {note}" in capsys.readouterr().out.splitlines()


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_guarded_target_still_reports_every_drift_and_its_unmanaged_folder(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    (root / ".factory" / "AGENTS.md").write_text("by hand\n", encoding="utf-8")
    (root / "CLAUDE.local.md").unlink()
    stray = root / ".claude" / "skills" / "stray"
    stray.mkdir()
    (stray / "SKILL.md").write_text("---\nname: stray\ndescription: x\n---\n", encoding="utf-8")

    check = check_overlay(overlay, None)

    assert _kinds(check, root) == {
        "CLAUDE.local.md": "missing",
        ".factory/AGENTS.md": "hand-edited",
        ".claude/skills/stray": "unmanaged",
    }
    assert check.code == 1
    assert (
        f"{shown(root)}: ~/.claude.json server entries were not compared while a hand-edited "
        "output holds this target back; resolve it, then run check again"
    ) in check.notes


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_an_unguarded_target_gets_no_held_back_note(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    (root / "CLAUDE.local.md").unlink()

    check = check_overlay(overlay, None)

    assert [note for note in check.notes if "were not compared" in note] == []


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_check_lists_tracked_team_skills_in_one_git_call_however_many_there_are(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    names = ("one", "two", "three")
    for name in names:
        team = root / ".claude" / "skills" / name
        team.mkdir(parents=True)
        (team / "SKILL.md").write_text(f"---\nname: {name}\ndescription: x\n---\n", "utf-8")
        _git(root, "add", "-f", f".claude/skills/{name}/SKILL.md")
    _git(root, "commit", "-q", "-m", "team skills")
    (root / ".claude" / "skills" / "three" / "stray.md").write_text("untracked\n", "utf-8")
    _sync(overlay)
    commands: list[tuple[str, ...]] = []
    run = git_runner.run

    def counted(
        folder: Path, *arguments: str, data: bytes | None = None
    ) -> subprocess.CompletedProcess[bytes]:
        commands.append(arguments)
        return run(folder, *arguments, data=data)

    monkeypatch.setattr(git_runner, "run", counted)

    check = check_overlay(overlay, None)

    listings = [arguments for arguments in commands if "ls-files" in arguments]
    team = [
        arguments
        for arguments in listings
        if any(f"skills/{name}" in argument for argument in arguments for name in names)
    ]
    assert len(team) == 1
    assert _kinds(check, root) == {".claude/skills/three": "unmanaged"}


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_the_global_ignore_hint_names_the_personal_paths_every_overlay_records(
    fake_home: Path,
) -> None:
    overlay, _ = _overlay(fake_home, "a")
    (overlay.folder / "loadout.toml").write_text(
        '[overlay]\ntargets = ["~/overlay-fixture*"]\n\n[[source]]\nname = "fixture"\n'
        'path = "."\n\n[claude]\ninstructions = ["personal"]\n',
        encoding="utf-8",
    )
    (overlay.folder / "permissions.toml").unlink()
    other = fake_home / "elsewhere"
    droid = other / ".factory" / "settings.local.json"
    entry = TargetRecord(
        common_dir=other / ".git",
        device=1,
        block=None,
        files={droid: text_entry(droid, "{}\n")},
        documents={},
        leaves={},
        pending={},
    )
    write_overlay_record(
        with_target(
            empty_record(fake_home / "overlays" / "other", "other", fake_home), other, entry
        )
    )
    _sync(overlay)

    check = check_overlay(overlay, None)

    hints = [note for note in check.notes if "are ignored only per repository" in note]
    assert [hint.split(" are ignored")[0] for hint in hints] == [
        "CLAUDE.local.md, .factory/settings.local.json"
    ]


def test_a_lone_surrogate_in_a_server_value_is_shown_escaped() -> None:
    assert overlay_check._dump({"url": "\ud800"}) == '{\n  "url": "\\ud800"\n}\n'


@pytest.mark.integration
@pytest.mark.usefixtures("isolated_git")
def test_a_document_that_cannot_be_merged_into_is_drift_with_exit_1_as_in_sync(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _sync(overlay)
    (root / SETTINGS).write_text('{"permissions": "oops"}\n', encoding="utf-8")

    check = check_overlay(overlay, None)
    synced = sync_overlay(OverlayRun(overlay, None, False, False))

    assert (check.code, synced.code) == (1, 1)
    assert check.errors == ()
    assert _kinds(check, root)[SETTINGS] == "unparsable"
    assert (
        f"~/{root.name}: .claude/settings.local.json: cannot write permissions.defaultMode: "
        "permissions is not a JSON object; loadout does not overwrite it — fix the value by hand"
    ) in check.notes
