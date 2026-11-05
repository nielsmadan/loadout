from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fixture_root import EXPECTED, OVERLAY_FIXTURES
from loadout import overlay_lock, overlay_sync
from loadout.exclude import GLOBAL_BEGIN, GLOBAL_END, block_lines
from loadout.overlay_git import GitFailure
from loadout.overlay_lock import overlay_lock as hold_overlay_lock
from loadout.overlay_record import overlay_record_path
from loadout.overlay_registry import RegisteredOverlay
from loadout.overlay_sync import OverlayOutcome, OverlayRun, report, sync_overlay

pytestmark = pytest.mark.integration

APPROVAL = "Bash(npm run x)"
SETTINGS = ".claude/settings.local.json"
EXPECTED_FILES = sorted(
    path.relative_to(EXPECTED / "overlay" / "target").as_posix()
    for path in (EXPECTED / "overlay" / "target").rglob("*")
    if path.is_file()
)


@pytest.fixture(autouse=True)
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


def _run(
    overlay: RegisteredOverlay, *, force: bool = False, interactive: bool = False
) -> OverlayRun:
    return OverlayRun(overlay=overlay, scope=None, force=force, interactive=interactive)


def _files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def _claude_json(home: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    return document


def _plan_codes(outcome: OverlayOutcome) -> dict[Path, int]:
    return {plan.root: plan.code for plan in outcome.plans}


def test_a_first_sync_writes_every_output_and_the_second_is_up_to_date(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, roots = _overlay(fake_home, "a", "b")

    first = sync_overlay(_run(overlay))

    assert first.code == 0
    assert first.errors == ()
    for root in roots:
        assert _files(root) == sorted(["AGENTS.md", *EXPECTED_FILES])
        assert _git(root, "status", "--porcelain") == ""
    projects = _claude_json(fake_home)["projects"]
    assert {str(root): sorted(projects[str(root)]["mcpServers"]) for root in roots} == {
        str(root): ["docs", "local-tool"] for root in roots
    }
    capsys.readouterr()

    second = sync_overlay(_run(overlay))
    report(second)

    out = capsys.readouterr().out
    assert second.code == 0
    assert [line for line in out.splitlines() if line.endswith("Up to date.")] == [
        f"overlay fixture: ~/{root.name}: Up to date." for root in roots
    ]


def test_a_team_tracked_output_is_refused_while_the_rest_still_renders(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    team = '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
    (root / "opencode.json").write_text(team, encoding="utf-8")
    _git(root, "add", "-f", "opencode.json")
    _git(root, "commit", "-q", "-m", "team opencode")
    personal = overlay.folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 3
    assert [refusal.kind for refusal in outcome.plans[0].refusals] == ["tracked"]
    assert (root / "opencode.json").read_text(encoding="utf-8") == team
    assert "Edited." in (root / "CLAUDE.local.md").read_text(encoding="utf-8")


def test_a_branch_tracking_an_output_keeps_the_teams_file_until_switching_back(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    rendered = (root / ".factory" / "AGENTS.md").read_text(encoding="utf-8")
    _git(root, "checkout", "-q", "-b", "team")
    (root / ".factory" / "AGENTS.md").write_text("Team droid.\n", encoding="utf-8")
    _git(root, "add", "-f", ".factory/AGENTS.md")
    _git(root, "commit", "-q", "-m", "team droid")

    on_team = sync_overlay(_run(overlay))

    assert on_team.code == 3
    assert (root / ".factory" / "AGENTS.md").read_text(encoding="utf-8") == "Team droid.\n"
    _git(root, "checkout", "-q", "main")
    assert not (root / ".factory" / "AGENTS.md").exists()

    back = sync_overlay(_run(overlay))

    assert back.code == 0
    assert (root / ".factory" / "AGENTS.md").read_text(encoding="utf-8") == rendered


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 folder")
def test_a_git_failure_leaves_that_target_untouched_and_syncs_the_other(fake_home: Path) -> None:
    overlay, (broken, healthy) = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    before = _files(broken)
    shutil.rmtree(broken / ".git" / "objects")
    (broken / ".git").chmod(0o000)
    personal = overlay.folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    try:
        outcome = sync_overlay(_run(overlay))
    finally:
        (broken / ".git").chmod(0o755)

    assert outcome.code == 3
    assert [(skip.folder, skip.kind) for skip in outcome.skipped] == [(broken, "git-failed")]
    assert _files(broken) == before
    assert "Edited." not in (broken / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert "Edited." in (healthy / "CLAUDE.local.md").read_text(encoding="utf-8")
    projects = _claude_json(fake_home)["projects"]
    assert sorted(projects[str(broken)]["mcpServers"]) == ["docs", "local-tool"]


def test_an_approval_saved_mid_sync_is_kept_beside_the_overlays_rules(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    settings = root / SETTINGS
    real = overlay_sync.replace_atomically
    saved: list[Path] = []

    def saving(path: Path, fill: Any, **options: Any) -> None:
        check = options.pop("before_rename")

        def approve_then_check() -> None:
            if path == settings and not saved:
                saved.append(path)
                settings.write_text(
                    json.dumps({"permissions": {"allow": [APPROVAL]}}, indent=2) + "\n",
                    encoding="utf-8",
                )
            check()

        real(path, fill, before_rename=approve_then_check, **options)

    monkeypatch.setattr(overlay_sync, "replace_atomically", saving)

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    expected = json.loads((EXPECTED / "overlay" / "target" / SETTINGS).read_text(encoding="utf-8"))
    document = json.loads(settings.read_text(encoding="utf-8"))
    assert document["permissions"]["allow"] == [*expected["permissions"]["allow"], APPROVAL]
    assert saved == [settings]


def test_an_accepted_global_offer_ignores_only_what_the_user_did_not(fake_home: Path) -> None:
    overlay, _ = _overlay(fake_home, "a")
    ignore = fake_home / ".config" / "git" / "ignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("**/CLAUDE.local.md\n", encoding="utf-8")
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    outcome = sync_overlay(_run(overlay, interactive=True), ask=ask)

    assert outcome.code == 0
    assert len(asked) == 1
    text = ignore.read_text(encoding="utf-8")
    assert text.startswith("**/CLAUDE.local.md\n")
    assert block_lines(text, GLOBAL_BEGIN, GLOBAL_END) == (
        "**/.claude/settings.local.json",
        "**/.factory/settings.local.json",
    )


def test_a_declined_global_offer_is_not_asked_again(fake_home: Path) -> None:
    overlay, _ = _overlay(fake_home, "a")
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "n"

    sync_overlay(_run(overlay, interactive=True), ask=ask)
    personal = overlay.folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    sync_overlay(_run(overlay, interactive=True), ask=ask)

    assert len(asked) == 1
    assert not (fake_home / ".config" / "git" / "ignore").exists()


def test_an_unreadable_record_refuses_until_force_which_carries_saved_approvals(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    settings = root / SETTINGS
    document = json.loads(settings.read_text(encoding="utf-8"))
    document["permissions"]["allow"].append(APPROVAL)
    settings.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    overlay_record_path(overlay.folder).write_text("{not json", encoding="utf-8")

    refused = sync_overlay(_run(overlay))
    forced = sync_overlay(_run(overlay, force=True))

    assert refused.code == 3
    assert refused.plans == ()
    assert str(overlay_record_path(overlay.folder)) in refused.errors[0]
    assert forced.code == 0
    allow = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert allow == document["permissions"]["allow"]
    assert allow[-1] == APPROVAL


def test_a_second_sync_while_the_lock_is_held_names_the_holder(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    monkeypatch.setattr(overlay_lock, "LOCK_TIMEOUT_SECONDS", 0.2)

    with hold_overlay_lock(overlay.folder):
        outcome = sync_overlay(_run(overlay))

    assert outcome.code == 3
    assert outcome.errors == (
        (
            f"another loadout process (pid {os.getpid()}) is syncing {overlay.folder}; "
            "wait for it to finish"
        ),
    )
    assert _files(root) == ["AGENTS.md"]


def test_a_user_level_server_of_the_same_name_refuses_the_overlay(fake_home: Path) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    codex = fake_home / ".codex" / "config.toml"
    codex.parent.mkdir()
    codex.write_text('[mcp_servers.docs]\nurl = "https://other.example/mcp"\n', encoding="utf-8")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 3
    assert outcome.errors == (
        (
            f"{codex} already defines the MCP server docs, which the overlay also defines; "
            "rename one of them"
        ),
    )
    assert _files(root) == ["AGENTS.md"]
    assert not (fake_home / ".claude.json").exists()


def test_a_team_negation_keeps_that_output_unwritten(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    (root / ".gitignore").write_text("!/CLAUDE.local.md\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "team negation")

    outcome = sync_overlay(_run(overlay))
    report(outcome)

    assert outcome.code == 3
    assert [(r.kind, r.message) for r in outcome.plans[0].refusals] == [
        (
            "unignored",
            "CLAUDE.local.md would show in git status: .gitignore:1:!/CLAUDE.local.md re-includes it",
        )
    ]
    assert not (root / "CLAUDE.local.md").exists()
    assert (root / ".factory" / "AGENTS.md").is_file()
    assert _git(root, "status", "--porcelain") == ""
    assert (
        f"overlay fixture: ~/{root.name}: {len(EXPECTED_FILES) - 1} written, 0 unchanged, 0 retired"
    ) in capsys.readouterr().out.splitlines()


def test_a_target_the_patterns_stop_matching_is_retired_whole(fake_home: Path) -> None:
    overlay, (kept_root, dropped) = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    manifest = overlay.folder / "loadout.toml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace("~/overlay-fixture*", "~/overlay-fixture-a"), encoding="utf-8")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    assert _files(dropped) == ["AGENTS.md"]
    assert _files(kept_root) == sorted(["AGENTS.md", *EXPECTED_FILES])
    projects = _claude_json(fake_home)["projects"]
    assert projects[str(dropped)]["mcpServers"] == {}
    assert sorted(projects[str(kept_root)]["mcpServers"]) == ["docs", "local-tool"]
    record = json.loads(overlay_record_path(overlay.folder).read_text(encoding="utf-8"))
    assert list(record["targets"]) == [str(kept_root)]
    assert "loadout overlay" not in (dropped / ".git" / "info" / "exclude").read_text(
        encoding="utf-8"
    )


def test_retiring_everything_empties_every_target_then_finishes(fake_home: Path) -> None:
    overlay, roots = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    finished: list[bool] = []
    run = OverlayRun(
        overlay=overlay,
        scope=None,
        force=False,
        interactive=False,
        retire_all=True,
        finish=lambda: finished.append(True),
    )

    outcome = sync_overlay(run)

    assert outcome.code == 0
    assert finished == [True]
    assert [_files(root) for root in roots] == [["AGENTS.md"], ["AGENTS.md"]]
    assert not (overlay.folder / ".loadout" / "opencode-instructions.md").exists()
    record = json.loads(overlay_record_path(overlay.folder).read_text(encoding="utf-8"))
    assert (record["targets"], record["shared"]) == ({}, {})


def _record(overlay: RegisteredOverlay) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        overlay_record_path(overlay.folder).read_text(encoding="utf-8")
    )
    return document


def _edit_manifest(overlay: RegisteredOverlay, old: str, new: str) -> None:
    manifest = overlay.folder / "loadout.toml"
    text = manifest.read_text(encoding="utf-8")
    assert old in text
    manifest.write_text(text.replace(old, new), encoding="utf-8")


def _set_servers(home: Path, root: Path, servers: dict[str, Any]) -> None:
    document = _claude_json(home)
    document["projects"][str(root)]["mcpServers"] = servers
    (home / ".claude.json").write_text(json.dumps(document, indent=2), encoding="utf-8")


def _approve(path: Path, rule: str) -> None:
    document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    document.setdefault("permissions", {}).setdefault("allow", []).append(rule)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _failing_unignored(monkeypatch: pytest.MonkeyPatch, root: Path, times: int) -> list[Path]:
    real = overlay_sync.unignored
    failed: list[Path] = []

    def unignored(at: Path, units: Any, folders: frozenset[str]) -> dict[str, str]:
        if at == root and len(failed) < times:
            failed.append(at)
            raise GitFailure(f"git check-ignore failed in {at}")
        return real(at, units, folders)

    monkeypatch.setattr(overlay_sync, "unignored", unignored)
    return failed


def _retire_all(
    overlay: RegisteredOverlay, finished: list[bool], *, force: bool = False
) -> OverlayRun:
    return OverlayRun(
        overlay=overlay,
        scope=None,
        force=force,
        interactive=False,
        retire_all=True,
        finish=lambda: finished.append(True),
    )


MINE = {"type": "stdio", "command": "mine"}


def test_a_server_the_user_already_removed_lets_its_retired_target_leave_the_record(
    fake_home: Path,
) -> None:
    overlay, (kept_root, dropped) = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    _set_servers(fake_home, dropped, {})
    _edit_manifest(overlay, "~/overlay-fixture*", "~/overlay-fixture-a")

    narrowed = sync_overlay(_run(overlay))

    assert narrowed.code == 0
    assert list(_record(overlay)["targets"]) == [str(kept_root)]
    _set_servers(fake_home, dropped, {"docs": MINE})

    forced = sync_overlay(_run(overlay, force=True))

    assert forced.code == 0
    assert _claude_json(fake_home)["projects"][str(dropped)]["mcpServers"] == {"docs": MINE}


def test_an_approval_saved_just_before_a_created_document_is_deleted_is_kept(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    settings = root / SETTINGS
    _edit_manifest(
        overlay, '[claude]\ninstructions = ["personal", "workflow"]\nsettings = "local"\n', ""
    )
    real = overlay_sync._delete
    saved: list[Path] = []

    def approve_then_delete(paths: Any, entry: Any, expected: Any) -> list[Path]:
        if settings in paths and not saved:
            saved.append(settings)
            _approve(settings, APPROVAL)
        return real(paths, entry, expected)

    monkeypatch.setattr(overlay_sync, "_delete", approve_then_delete)

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    assert saved == [settings]
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "permissions": {"allow": [APPROVAL]}
    }
    assert not (root / "CLAUDE.local.md").exists()


def test_a_git_failure_before_a_document_is_written_leaves_no_false_refusal_behind(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    settings = root / SETTINGS
    settings.parent.mkdir()
    _approve(settings, APPROVAL)
    failed = _failing_unignored(monkeypatch, root, 1)

    first = sync_overlay(_run(overlay))

    assert first.code == 3
    assert failed == [root]
    entry = _record(overlay)["targets"][str(root)]
    assert (entry["written"], entry["documents"]) == ({}, {})

    second = sync_overlay(_run(overlay))

    assert second.code == 0
    assert second.errors == ()
    expected = json.loads((EXPECTED / "overlay" / "target" / SETTINGS).read_text(encoding="utf-8"))
    allow = json.loads(settings.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert sorted(allow) == sorted([*expected["permissions"]["allow"], APPROVAL])


def test_a_replan_keeps_owning_the_rules_of_a_document_written_before_it(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (raced, calm) = _overlay(fake_home, "a", "b")
    droid = raced / ".factory" / "settings.local.json"
    real = overlay_sync.replace_atomically
    saved: list[Path] = []

    def saving(path: Path, fill: Any, **options: Any) -> None:
        check = options.pop("before_rename")

        def save_then_check() -> None:
            if path == droid and not saved:
                assert (raced / SETTINGS).is_file()
                saved.append(path)
                droid.write_text('{\n  "note": "saved by droid"\n}\n', encoding="utf-8")
            check()

        real(path, fill, before_rename=save_then_check, **options)

    monkeypatch.setattr(overlay_sync, "replace_atomically", saving)

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    assert saved == [droid]
    assert json.loads(droid.read_text(encoding="utf-8"))["note"] == "saved by droid"
    targets = _record(overlay)["targets"]
    raced_entry = targets[str(raced)]["documents"][str(raced / SETTINGS)]
    calm_entry = targets[str(calm)]["documents"][str(calm / SETTINGS)]
    assert raced_entry == calm_entry
    assert raced_entry["created"] is True
    assert [rule["path"] for rule in raced_entry["owned_rules"]] == [
        rule["path"] for rule in calm_entry["owned_rules"]
    ]
    assert len(raced_entry["owned_rules"]) == 3


def test_a_lone_surrogate_in_one_target_document_neither_crashes_nor_stops_the_others(
    fake_home: Path,
) -> None:
    overlay, (odd, healthy) = _overlay(fake_home, "a", "b")
    settings = odd / SETTINGS
    settings.parent.mkdir()
    settings.write_text('{"note": "\\ud800"}\n', encoding="utf-8")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    assert '"note": "\\ud800"' in settings.read_text(encoding="utf-8")
    assert json.loads(settings.read_text(encoding="utf-8"))["note"] == "\ud800"
    assert (healthy / "CLAUDE.local.md").is_file()
    assert sorted(_claude_json(fake_home)["projects"]) == sorted([str(odd), str(healthy)])
    again = sync_overlay(_run(overlay))
    assert again.code == 0
    assert {plan.root: dict(plan.writes) for plan in again.plans} == {odd: {}, healthy: {}}


def test_retiring_everything_leaves_a_team_tracked_output_in_place_and_finishes(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    _git(root, "add", "-f", "CLAUDE.local.md")
    _git(root, "commit", "-q", "-m", "team local")
    finished: list[bool] = []
    capsys.readouterr()

    outcome = sync_overlay(_retire_all(overlay, finished))
    report(outcome)

    assert outcome.code == 0
    assert finished == [True]
    assert _files(root) == ["AGENTS.md", "CLAUDE.local.md"]
    assert _record(overlay)["targets"] == {}
    assert (
        f"note: overlay fixture: ~/{root.name}: the team tracks CLAUDE.local.md; it is left in "
        "place and no longer recorded"
    ) in capsys.readouterr().out.splitlines()


def test_a_claude_json_that_cannot_be_used_stops_only_the_servers(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    monkeypatch.setenv("CLAUDE_CODE_CUSTOM_OAUTH_URL", "https://login.example")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 3
    assert _files(root) == sorted(["AGENTS.md", *EXPECTED_FILES])
    message = (
        f"Claude Code reads {fake_home / '.claude-custom-oauth.json'} while "
        f"CLAUDE_CODE_CUSTOM_OAUTH_URL is set, not {fake_home / '.claude.json'}; unset it to "
        "sync overlay servers"
    )
    assert outcome.errors == (message,)
    assert not (fake_home / ".claude.json").exists()


def test_an_unmatched_target_that_cannot_be_examined_is_reported_and_kept(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, (kept_root, broken) = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    _edit_manifest(overlay, '"~/overlay-fixture*"', '"~/overlay-fixture-a"')
    (broken / ".git" / "HEAD").write_text("garbage\n", encoding="utf-8")
    capsys.readouterr()

    outcome = sync_overlay(_run(overlay))
    report(outcome)

    assert outcome.code == 0
    assert [(skip.folder, skip.kind) for skip in outcome.skipped] == [(broken, "unexamined")]
    assert (
        f"overlay fixture: ~/{broken.name}: skipped: no longer matched by `targets`, but it does "
        "not probe as a checkout; its outputs are kept"
    ) in capsys.readouterr().err.splitlines()
    assert (broken / "CLAUDE.local.md").is_file()
    assert sorted(_record(overlay)["targets"]) == sorted([str(kept_root), str(broken)])


def test_a_whole_file_edited_after_planning_is_not_overwritten(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    personal = overlay.folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")
    local = root / "CLAUDE.local.md"
    real = overlay_sync._apply_steps

    def edited_first(*args: Any, **kwargs: Any) -> Any:
        if "By hand." not in local.read_text(encoding="utf-8"):
            local.write_text(local.read_text(encoding="utf-8") + "By hand.\n", encoding="utf-8")
        return real(*args, **kwargs)

    monkeypatch.setattr(overlay_sync, "_apply_steps", edited_first)

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 1
    assert [(r.kind, r.path) for r in outcome.plans[0].refusals] == [
        ("guard", local),
        ("abort", None),
    ]
    assert local.read_text(encoding="utf-8").endswith("By hand.\n")
    assert "Edited." not in local.read_text(encoding="utf-8")


def _case_blind(root: Path) -> None:
    if not (root / ".GIT").exists():
        pytest.skip("the temporary file system is case-sensitive")


def test_a_team_file_tracked_under_another_case_spelling_is_refused_not_merged(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _case_blind(root)
    team = '{\n  "$schema": "https://opencode.ai/config.json"\n}\n'
    (root / "Opencode.json").write_text(team, encoding="utf-8")
    _git(root, "add", "Opencode.json")
    _git(root, "commit", "-q", "-m", "team opencode")

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 3
    assert [refusal.kind for refusal in outcome.plans[0].refusals] == ["tracked"]
    assert (root / "Opencode.json").read_text(encoding="utf-8") == team
    assert (root / "CLAUDE.local.md").is_file()
    assert _git(root, "status", "--porcelain") == ""


def test_retiring_everything_leaves_a_case_variant_team_file_in_place(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    _case_blind(root)
    sync_overlay(_run(overlay))
    rules = root / ".codex" / "rules" / "fixture.rules"
    text = rules.read_text(encoding="utf-8")
    rules.unlink()
    (rules.parent / "Fixture.rules").write_text(text, encoding="utf-8")
    _git(root, "add", "-f", ".codex/rules/Fixture.rules")
    _git(root, "commit", "-q", "-m", "team rules")
    finished: list[bool] = []
    capsys.readouterr()

    outcome = sync_overlay(_retire_all(overlay, finished))
    report(outcome)

    assert outcome.code == 0
    assert finished == [True]
    assert (rules.parent / "Fixture.rules").read_text(encoding="utf-8") == text
    assert _git(root, "status", "--porcelain") == ""
    assert (
        f"note: overlay fixture: ~/{root.name}: the team tracks .codex/rules/fixture.rules; it "
        "is left in place and no longer recorded"
    ) in capsys.readouterr().out.splitlines()


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 folder")
def test_retiring_everything_with_force_drops_an_unreachable_target_and_strips_its_servers(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    locked = fake_home / "locked"
    far = locked / "overlay-fixture-c"
    far.mkdir(parents=True)
    _git(far, "init", "-q", "-b", "main")
    (far / "AGENTS.md").write_text("Team instructions.\n", encoding="utf-8")
    _git(far, "add", "AGENTS.md")
    _git(far, "commit", "-q", "-m", "team")
    far = far.resolve()
    _edit_manifest(overlay, '"~/overlay-fixture*"', '"~/overlay-fixture*", "~/locked/overlay-*"')
    assert sync_overlay(_run(overlay)).code == 0
    finished: list[bool] = []
    locked.chmod(0o000)
    try:
        refused = sync_overlay(_retire_all(overlay, finished))
        kept_targets = list(_record(overlay)["targets"])
        forced = sync_overlay(_retire_all(overlay, finished, force=True))
    finally:
        locked.chmod(0o755)

    assert refused.code == 3
    assert kept_targets == [str(far)]
    assert forced.code == 0
    assert finished == [True]
    assert _record(overlay)["targets"] == {}
    assert _claude_json(fake_home)["projects"][str(far)]["mcpServers"] == {}
    assert _files(root) == ["AGENTS.md"]
    assert "CLAUDE.local.md" in _files(far)
    (note,) = forced.plans[0].notes
    assert note.startswith(
        "cannot be reached; --force drops it from the record and leaves its outputs in place: "
    )
    assert "CLAUDE.local.md" in note


def test_the_shared_file_is_kept_while_a_target_still_using_it_failed(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (healthy, broken) = _overlay(fake_home, "a", "b")
    sync_overlay(_run(overlay))
    shared = overlay.folder / ".loadout" / "opencode-instructions.md"
    _edit_manifest(overlay, '[opencode]\ninstructions = ["workflow", "personal"]\n', "")
    _failing_unignored(monkeypatch, broken, 1)

    failed = sync_overlay(_run(overlay))

    assert failed.code == 3
    assert shared.is_file()
    assert list(_record(overlay)["shared"]) == [str(shared)]
    assert str(shared) in (broken / "opencode.json").read_text(encoding="utf-8")

    healed = sync_overlay(_run(overlay))

    assert healed.code == 0
    assert _record(overlay)["shared"] == {}
    assert not shared.exists()
    assert (healthy / "AGENTS.md").is_file()


def test_retiring_a_document_holding_an_approval_does_not_offer_the_global_ignore(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))
    _approve(root / SETTINGS, APPROVAL)
    asked: list[str] = []
    finished: list[bool] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return "y"

    run = replace(_retire_all(overlay, finished), interactive=True)
    outcome = sync_overlay(run, ask=ask)

    assert outcome.code == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {
        "permissions": {"allow": [APPROVAL]}
    }
    assert asked == []


def test_the_global_block_follows_the_records_as_they_stand_when_it_is_written(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    ignore = fake_home / ".config" / "git" / "ignore"
    ignore.parent.mkdir(parents=True)
    ignore.write_text("**/CLAUDE.local.md\n", encoding="utf-8")
    real = overlay_sync.write_global_block

    def another_sync_first(file: Path, patterns: Any) -> bool:
        record = _record(overlay)
        del record["targets"][str(root)]["documents"][str(root / ".factory/settings.local.json")]
        path = overlay_record_path(overlay.folder)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        return real(file, patterns)

    monkeypatch.setattr(overlay_sync, "write_global_block", another_sync_first)

    outcome = sync_overlay(_run(overlay, interactive=True), ask=lambda prompt: "y")

    assert outcome.code == 0
    text = ignore.read_text(encoding="utf-8")
    assert block_lines(text, GLOBAL_BEGIN, GLOBAL_END) == ("**/.claude/settings.local.json",)


def test_a_finish_that_fails_with_an_os_error_is_reported_with_the_plans(
    fake_home: Path,
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    sync_overlay(_run(overlay))

    def finish() -> None:
        raise OSError("registry is read-only")

    run = replace(_retire_all(overlay, []), finish=finish)
    outcome = sync_overlay(run)

    assert outcome.code == 3
    assert outcome.errors == ("registry is read-only",)
    assert [plan.root for plan in outcome.plans] == [root]


def test_a_server_written_before_a_crash_is_updated_after_a_source_edit(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay, (root,) = _overlay(fake_home, "a")
    real = overlay_sync.write_leaves
    crashed: list[Path] = []

    def crash_after_writing(path: Path, *args: Any, **options: Any) -> Any:
        result = real(path, *args, **options)
        if not crashed:
            crashed.append(path)
            raise OSError("killed before the record was settled")
        return result

    monkeypatch.setattr(overlay_sync, "write_leaves", crash_after_writing)
    assert sync_overlay(_run(overlay)).code == 3
    assert list(_record(overlay)["targets"][str(root)]["pending"]) == [
        str(fake_home / ".claude.json")
    ]
    mcp = overlay.folder / "mcp.toml"
    mcp.write_text(
        mcp.read_text(encoding="utf-8").replace("docs.example", "docs2.example"), encoding="utf-8"
    )

    outcome = sync_overlay(_run(overlay))

    assert outcome.code == 0
    servers = _claude_json(fake_home)["projects"][str(root)]["mcpServers"]
    assert servers["docs"]["url"] == "https://docs2.example/mcp"
