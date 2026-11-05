from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import loadout
from fixture_root import OVERLAY_FIXTURES, build_root
from loadout import overlay_record, overlay_sync
from loadout.exclude import (
    BLOCK_BEGIN,
    GlobalIgnoreState,
    global_pattern,
    write_global_state,
)
from loadout.init_workflow import INTERRUPTED
from loadout.machine import config_dir
from loadout.overlay_record import (
    RECORDS_DIR,
    empty_record,
    overlay_record_path,
    write_overlay_record,
)
from loadout.overlay_registry import load_registry, overlays_path
from registration import register

pytestmark = pytest.mark.integration

NAME = "fixture"
LOCAL = "CLAUDE.local.md"
SETTINGS = ".claude/settings.local.json"
APPROVAL = "Bash(git status:*)"


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


def _repo(home: Path, name: str) -> Path:
    root = home / name
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / "AGENTS.md").write_text("Team instructions.\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-q", "-m", "team")
    return root.resolve()


def _overlay(home: Path, *names: str) -> tuple[Path, list[Path]]:
    folder = home / "overlays" / NAME
    shutil.copytree(OVERLAY_FIXTURES, folder)
    roots = [_repo(home, f"overlay-fixture-{name}") for name in names]
    assert loadout.main(["overlay", "add", str(folder)]) == 0
    return folder.resolve(), roots


def _edit_source(folder: Path) -> None:
    personal = folder / "instructions" / "personal.md"
    personal.write_text(personal.read_text(encoding="utf-8") + "\nEdited.\n", encoding="utf-8")


def _files(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    )


def _record(folder: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(overlay_record_path(folder).read_text(encoding="utf-8"))
    return document


def _global_source(tmp_path: Path) -> Path:
    source = register(build_root(tmp_path))
    assert loadout.main(["sync", "--root", str(source), "--force"]) == 0
    return source


def _stamps(root: Path) -> dict[str, int]:
    return {name: (root / name).stat().st_mtime_ns for name in _files(root)}


def test_sync_in_a_target_syncs_only_that_target(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, (first, second) = _overlay(fake_home, "a", "b")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _edit_source(folder)
    untouched = _record(folder)["targets"][str(second)]
    stamps = _stamps(second)
    monkeypatch.chdir(first / ".claude")

    assert loadout.main(["sync"]) == 0

    assert "Edited." in (first / LOCAL).read_text(encoding="utf-8")
    assert "Edited." not in (second / LOCAL).read_text(encoding="utf-8")
    assert _stamps(second) == stamps
    assert _record(folder)["targets"][str(second)] == untouched


def test_sync_in_the_overlay_folder_or_by_name_syncs_every_target(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, roots = _overlay(fake_home, "a", "b")
    monkeypatch.chdir(folder)
    assert loadout.main(["sync"]) == 0
    assert all((root / LOCAL).is_file() for root in roots)
    _edit_source(folder)
    elsewhere = fake_home / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert all("Edited." in (root / LOCAL).read_text(encoding="utf-8") for root in roots)
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", "nope"]) == 3
    assert "no overlay named nope is registered; registered overlays: fixture" in (
        capsys.readouterr().err
    )


def test_global_sync_writes_the_global_source_and_every_healthy_target(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _global_source(tmp_path)
    primary = fake_home / ".harness-a" / "PRIMARY.md"
    primary.unlink()
    _, (healthy, broken) = _overlay(fake_home, "a", "b")
    (broken / "opencode.json").write_text("{not json\n", encoding="utf-8")

    assert loadout.main(["sync", "--global"]) == 3

    out = capsys.readouterr()
    assert primary.is_file()
    assert (healthy / LOCAL).is_file()
    assert (broken / "opencode.json").read_text(encoding="utf-8") == "{not json\n"
    assert f"overlay {NAME}: ~/overlay-fixture-b: refused: opencode.json is not a JSON " in out.err


def test_global_force_does_not_force_an_overlay_and_names_the_overlay_command(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _global_source(tmp_path)
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--global"]) == 0
    edited = (root / LOCAL).read_text(encoding="utf-8") + "Mine.\n"
    (root / LOCAL).write_text(edited, encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--global", "--force"]) == 1
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert f"run `loadout sync --overlay {NAME} --force` to discard them" in (
        capsys.readouterr().err
    )

    assert loadout.main(["sync", "--overlay", NAME, "--force"]) == 0
    assert "Mine." not in (root / LOCAL).read_text(encoding="utf-8")


def test_an_owned_rule_removed_on_disk_exits_1_until_force(fake_home: Path) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    path = root / SETTINGS
    document = json.loads(path.read_text(encoding="utf-8"))
    assert APPROVAL in document["permissions"]["allow"]
    document["permissions"]["allow"].remove(APPROVAL)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    assert loadout.main(["sync", "--overlay", NAME]) == 1
    assert APPROVAL not in json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]
    assert loadout.main(["sync", "--overlay", NAME, "--force"]) == 0
    assert APPROVAL in json.loads(path.read_text(encoding="utf-8"))["permissions"]["allow"]


def test_an_unreadable_own_record_exits_3_until_force(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    overlay_record_path(folder).write_text("{broken", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 3
    assert str(overlay_record_path(folder)) in capsys.readouterr().err
    assert loadout.main(["sync", "--overlay", NAME, "--force"]) == 0
    assert str(root) in _record(folder)["targets"]


def test_a_folder_without_git_that_the_pattern_matches_is_skipped_at_exit_0(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    (fake_home / "overlay-fixture-plain").mkdir()

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert (root / LOCAL).is_file()
    assert f"overlay {NAME}: ~/overlay-fixture-plain: skipped: " in capsys.readouterr().err


def test_usage_errors_exit_2(fake_home: Path) -> None:
    assert loadout.main(["sync", "--overlay", "x", "--profile", "p"]) == 2
    assert loadout.main(["check", "--overlay", "x", "--staged"]) == 2
    assert loadout.main(["sync", "--overlay", "x", "--global"]) == 2
    folder = fake_home / "overlays" / "named"
    shutil.copytree(OVERLAY_FIXTURES, folder)
    assert loadout.main(["overlay", "add", str(folder), "--name", "Not_Valid"]) == 2
    assert loadout.main(["overlay", "add", str(folder), "--name", "valid"]) == 0


def test_a_repository_no_overlay_targets_and_without_a_manifest_exits_3(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _overlay(fake_home, "a")
    other = _repo(fake_home, "unrelated")
    monkeypatch.chdir(other)

    assert loadout.main(["sync"]) == 3
    assert f"and no registered overlay targets {other}" in capsys.readouterr().err


def _project(home: Path) -> Path:
    root = _repo(home, "overlay-fixture-project")
    (root / "loadout" / "instructions").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text(
        'harnesses = ["claude"]\ninstructions = ["team"]\n', encoding="utf-8"
    )
    (root / "loadout" / "instructions" / "team.md").write_text("Team.\n", encoding="utf-8")
    return root


def test_a_project_the_overlay_never_wrote_is_skipped_and_checks_as_one_object(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _overlay(fake_home)
    root = _project(fake_home)
    monkeypatch.chdir(root)
    skipped = (
        f"overlay {NAME}: ~/{root.name}: skipped: adopted loadout (it has loadout/config.toml); "
        "the overlay does not write here"
    )

    assert loadout.main(["sync"]) == 0
    assert skipped in capsys.readouterr().err
    assert (root / "CLAUDE.md").is_file()
    (root / "CLAUDE.md").unlink()

    assert loadout.main(["check", "--json"]) == 1

    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["exit_code"], result["errors"]) == ("drift", 1, [])
    assert result["drift"] == [{"path": str(root / "CLAUDE.md"), "kind": "missing"}]
    assert skipped in result["notes"]


def _registry_names() -> dict[str, bool]:
    return {overlay.name: overlay.removing for overlay in load_registry(overlays_path())}


def _assert_removed(folder: Path, roots: list[Path], home: Path) -> None:
    for root in roots:
        assert _files(root) == ["AGENTS.md"]
        exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert BLOCK_BEGIN.format(name=NAME) not in exclude
    projects = json.loads((home / ".claude.json").read_text(encoding="utf-8")).get("projects", {})
    assert [projects.get(str(root), {}).get("mcpServers", {}) for root in roots] == [
        {} for _ in roots
    ]
    assert not overlay_record_path(folder).exists()
    assert NAME not in _registry_names()


def test_remove_retires_every_output_and_unregisters(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, roots = _overlay(fake_home, "a", "b")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert all((root / LOCAL).is_file() for root in roots)
    capsys.readouterr()

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    assert f"removed overlay {NAME}" in capsys.readouterr().out
    _assert_removed(folder, roots, fake_home)


def test_a_deleted_target_is_reported_as_gone_and_its_servers_retired(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (kept, gone) = _overlay(fake_home, "a", "b")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    shutil.rmtree(gone)
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    out = capsys.readouterr().out.splitlines()
    assert f"overlay {NAME}: ~/overlay-fixture-b: retired: the folder is gone" in out
    assert f"overlay {NAME}: ~/overlay-fixture-a: Up to date." in out
    projects = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))["projects"]
    assert sorted(projects[str(kept)]["mcpServers"]) == ["docs", "local-tool"]
    assert projects[str(gone)]["mcpServers"] == {}


def test_remove_takes_the_block_out_when_a_document_the_user_had_is_left_behind(
    fake_home: Path,
) -> None:
    root = _repo(fake_home, "overlay-fixture-a")
    (root / "opencode.json").write_text(
        '{"$schema": "https://opencode.ai/config.json"}\n', encoding="utf-8"
    )
    folder, _ = _overlay(fake_home)
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert "mcp" in json.loads((root / "opencode.json").read_text(encoding="utf-8"))

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    assert json.loads((root / "opencode.json").read_text(encoding="utf-8")) == {
        "$schema": "https://opencode.ai/config.json"
    }
    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert BLOCK_BEGIN.format(name=NAME) not in exclude
    assert _git(root, "status", "--porcelain") == "?? opencode.json\n"
    assert not overlay_record_path(folder).exists()


def test_remove_completes_from_the_record_after_the_overlay_folder_is_deleted(
    fake_home: Path,
) -> None:
    folder, roots = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    shutil.rmtree(folder)

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    _assert_removed(folder, roots, fake_home)


def test_remove_of_a_hand_edited_output_stops_and_leaves_the_overlay_being_removed(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    edited = (root / LOCAL).read_text(encoding="utf-8") + "Mine.\n"
    (root / LOCAL).write_text(edited, encoding="utf-8")

    capsys.readouterr()

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 1
    err = capsys.readouterr().err
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert _registry_names() == {NAME: True}
    assert overlay_record_path(folder).is_file()
    assert (
        "Removal stopped — the '-' lines would be lost. "
        f"Run `loadout overlay remove {NAME} --force` to discard them."
    ) in err
    assert err.endswith(
        f"overlay {NAME}: not removed; the registration and record are kept — fix the "
        f"problems above, then run `loadout overlay remove {NAME}` again\n"
    )

    assert loadout.main(["sync", "--overlay", NAME]) == 3
    assert f"being removed; run `loadout overlay remove {NAME}`" in capsys.readouterr().err

    assert loadout.main(["overlay", "remove", NAME, "--yes", "--force"]) == 0
    _assert_removed(folder, [root], fake_home)


def test_remove_of_a_server_edited_in_claude_json_names_the_remove_force_command(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    path = fake_home / ".claude.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["projects"][str(root)]["mcpServers"]["docs"]["command"] = "mine"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 1
    assert (
        f"overlay {NAME}: ~/overlay-fixture-a: refused: MCP server docs was edited outside "
        f"loadout; run `loadout overlay remove {NAME} --force` to retire it anyway"
    ) in capsys.readouterr().err
    assert _registry_names() == {NAME: True}

    assert loadout.main(["overlay", "remove", NAME, "--yes", "--force"]) == 0
    _assert_removed(folder, [root], fake_home)


def test_remove_leaves_a_team_tracked_output_in_place_and_still_finishes(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _git(root, "add", "-f", "opencode.json")
    _git(root, "commit", "-q", "-m", "team opencode")
    team = (root / "opencode.json").read_text(encoding="utf-8")

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    assert (root / "opencode.json").read_text(encoding="utf-8") == team
    assert "the team tracks opencode.json; it is left in place" in capsys.readouterr().out
    assert not overlay_record_path(folder).exists()


def test_remove_works_while_another_overlays_record_is_unreadable(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, roots = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    foreign = config_dir() / RECORDS_DIR / "0000000000000000.json"
    foreign.write_text("{broken", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 3
    assert str(foreign) in capsys.readouterr().err

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0
    _assert_removed(folder, roots, fake_home)
    assert foreign.read_text(encoding="utf-8") == "{broken"


def test_an_unreadable_record_of_a_registered_overlay_names_its_remove_command(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _repo(fake_home, "elsewhere")
    other = fake_home / "overlays" / "other"
    (other / "instructions").mkdir(parents=True)
    (other / "instructions" / "other.md").write_text("# Other\n", encoding="utf-8")
    (other / "loadout.toml").write_text(
        '[overlay]\ntargets = ["~/elsewhere*"]\n\n[[source]]\nname = "other"\npath = "."\n\n'
        '[pi]\ninstructions = ["other"]\n',
        encoding="utf-8",
    )
    assert loadout.main(["overlay", "add", str(other)]) == 0
    overlay_record_path(folder).write_text("{broken", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", "other"]) == 3

    assert (
        f"the overlay record {overlay_record_path(folder)} cannot be read; fix it, or run "
        f"`loadout overlay remove {NAME} --force`"
    ) in capsys.readouterr().err


def test_remove_without_a_terminal_needs_yes(fake_home: Path) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert loadout.main(["overlay", "remove", NAME]) == 2

    assert (root / LOCAL).is_file()
    assert _registry_names() == {NAME: False}


def test_list_names_an_unregistered_record_and_remove_takes_it_by_name(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, roots = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    overlays_path().unlink()
    capsys.readouterr()

    assert loadout.main(["overlay", "list"]) == 0
    assert capsys.readouterr().out == (
        f"{NAME}  {folder}  (unregistered record; remove it with `loadout overlay remove {NAME}`)\n"
    )

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0
    _assert_removed(folder, roots, fake_home)


def test_init_project_in_a_target_exits_3(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    monkeypatch.chdir(root)
    capsys.readouterr()

    assert loadout.main(["init", "project", "--dry-run"]) == 3
    assert (
        f"remove this repository from overlay {NAME} first: narrow its `targets`, then run "
        f"`loadout sync --overlay {NAME}`"
    ) in capsys.readouterr().err


def test_a_server_edited_in_claude_json_names_the_overlay_force_command(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    path = fake_home / ".claude.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["projects"][str(root)]["mcpServers"]["docs"]["command"] = "mine"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 1
    captured = capsys.readouterr()
    assert (
        f"overlay {NAME}: ~/overlay-fixture-a: refused: MCP server docs was edited outside "
        f"loadout; run `loadout sync --overlay {NAME} --force` to replace it"
    ) in captured.err
    assert f"overlay {NAME}: ~/overlay-fixture-a: 0 written, " in captured.out


def test_remove_force_clears_an_unreadable_own_record_but_never_a_newer_one(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _overlay(fake_home, "a")
    record = overlay_record_path(folder)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text('{"version": 99}', encoding="utf-8")

    assert loadout.main(["overlay", "remove", NAME, "--yes", "--force"]) == 3
    assert "written by a newer loadout; upgrade loadout" in capsys.readouterr().err
    assert record.read_text(encoding="utf-8") == '{"version": 99}'
    assert _registry_names() == {NAME: False}

    record.write_text("{broken", encoding="utf-8")
    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 3
    assert f"`loadout overlay remove {NAME} --force` to treat it as empty" in (
        capsys.readouterr().err
    )
    assert _registry_names() == {NAME: False}
    assert loadout.main(["overlay", "remove", NAME, "--yes", "--force"]) == 0
    assert not record.exists()
    assert NAME not in _registry_names()


def test_a_broken_registry_still_syncs_and_checks_the_global_source(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _global_source(tmp_path)
    primary = fake_home / ".harness-a" / "PRIMARY.md"
    primary.unlink()
    registry = overlays_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("[broken\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--global"]) == 3
    assert primary.is_file()
    assert f"loadout: {registry}" in capsys.readouterr().err

    primary.unlink()
    assert loadout.main(["check", "--global", "--json"]) == 3
    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["exit_code"]) == ("error", 3)
    assert result["drift"] == [{"path": str(primary), "kind": "missing"}]
    assert [(e["overlay"], e["target"]) for e in result["errors"]] == [(None, None)]
    assert result["errors"][0]["error"].startswith(str(registry))


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a chmod 000 file")
def test_an_unreadable_global_fragment_still_lets_every_overlay_run(
    fake_home: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _global_source(tmp_path)
    _, (root,) = _overlay(fake_home, "a")
    fragment = source / "instructions" / "intro.md"
    fragment.chmod(0o000)
    capsys.readouterr()
    try:
        synced = loadout.main(["sync", "--global"])
        err = capsys.readouterr().err
        checked = loadout.main(["check", "--global", "--json"])
        result = json.loads(capsys.readouterr().out)
    finally:
        fragment.chmod(0o644)

    assert (synced, checked) == (3, 3)
    assert f"loadout: {fragment}: Permission denied" in err.splitlines()
    assert (root / LOCAL).is_file()
    assert [(e["overlay"], e["error"]) for e in result["errors"]] == [
        (None, f"{fragment}: Permission denied")
    ]


def test_check_json_errors_carry_the_absolute_target_even_with_a_space(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (healthy,) = _overlay(fake_home, "a")
    spaced = _repo(fake_home, "overlay-fixture-my app")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    (healthy / LOCAL).unlink()
    (spaced / "opencode.json").write_text("{not json\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME, "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert result["drift"] == [{"path": str(healthy / LOCAL), "kind": "missing"}]
    assert [(e["overlay"], e["target"]) for e in result["errors"]] == [(NAME, str(spaced))]
    assert result["errors"][0]["error"].startswith("refused: opencode.json is not a JSON ")


def test_a_registered_overlay_folder_whose_manifest_does_not_parse_reports_the_toml_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _overlay(fake_home, "a")
    manifest = folder / "loadout.toml"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "oops = [\n", encoding="utf-8")
    monkeypatch.chdir(folder)
    capsys.readouterr()

    assert loadout.main(["sync"]) == 3
    assert f"overlay {NAME}: ERROR: {manifest}: invalid TOML" in capsys.readouterr().err


def _accept_global_ignore(file: Path) -> None:
    write_global_state(GlobalIgnoreState("accepted", file))


def test_remove_rewrites_the_global_ignore_block_without_the_overlay(fake_home: Path) -> None:
    ignore = fake_home / "global-ignore"
    _accept_global_ignore(ignore)
    _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert global_pattern(LOCAL) in ignore.read_text(encoding="utf-8").splitlines()

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    assert global_pattern(LOCAL) not in ignore.read_text(encoding="utf-8").splitlines()


def test_remove_says_it_finished_when_only_the_global_ignore_update_failed(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _accept_global_ignore(fake_home / "global-ignore")
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    blocker = fake_home / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    _accept_global_ignore(blocker / "ignore")
    capsys.readouterr()

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 3

    err = capsys.readouterr().err
    assert f"overlay {NAME}: ERROR: global gitignore: " in err
    assert err.endswith(
        f"overlay {NAME}: removed — its registration and record are gone, but the step above "
        "failed, so the global gitignore block may still list its files until another overlay "
        "syncs\n"
    )
    _assert_removed(folder, [root], fake_home)


def test_remove_deletes_a_readable_record_kept_under_a_stale_file_name(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _overlay(fake_home, "a")
    write_overlay_record(empty_record(folder, NAME, fake_home))
    stale = overlay_record_path(folder).with_name("0000000000000000.json")
    overlay_record_path(folder).rename(stale)

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0

    assert sorted(stale.parent.iterdir()) == []
    capsys.readouterr()
    assert loadout.main(["overlay", "list"]) == 0
    assert capsys.readouterr().out == (
        "no overlays registered; add one with `loadout overlay add <folder>`\n"
    )


def test_init_project_is_refused_while_an_overlay_record_cannot_be_read(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(fake_home, "unrelated")
    broken = config_dir() / RECORDS_DIR / "0000000000000000.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{broken", encoding="utf-8")

    assert loadout.main(["init", "project", "--root", str(root), "--dry-run"]) == 3

    assert (
        "an overlay record cannot be read, so loadout cannot tell whether an overlay writes "
        f"personal files here; the overlay record {broken} cannot be read"
    ) in capsys.readouterr().err
    assert _files(root) == ["AGENTS.md"]


def test_a_git_failure_outside_a_manifest_exits_3_and_warns_in_a_project(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _overlay(fake_home, "a")
    plain = _repo(fake_home, "unrelated")
    project = _project(fake_home)
    (fake_home / "test-gitconfig").write_text("[broken\n", encoding="utf-8")
    capsys.readouterr()

    monkeypatch.chdir(plain)
    assert loadout.main(["sync"]) == 3
    assert f"loadout: cannot inspect Git repository {plain}: " in capsys.readouterr().err

    monkeypatch.chdir(project)
    loadout.main(["check"])
    assert f"WARNING: overlays: cannot inspect Git repository {project}: " in (
        capsys.readouterr().err
    )


def test_global_without_a_machine_config_still_writes_the_overlays_and_exits_3(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    capsys.readouterr()

    assert loadout.main(["sync", "--global"]) == 3

    assert (root / LOCAL).is_file()
    assert "run `loadout init global` first" in capsys.readouterr().err


def _adopt(root: Path) -> None:
    (root / "loadout" / "instructions").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text(
        'harnesses = ["claude"]\ninstructions = ["team"]\n', encoding="utf-8"
    )
    (root / "loadout" / "instructions" / "team.md").write_text("Team.\n", encoding="utf-8")
    _git(root, "add", "loadout")
    _git(root, "commit", "-q", "-m", "adopt loadout")


def test_check_staged_in_a_target_runs_the_project_check_alone(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _adopt(root)
    monkeypatch.chdir(root)
    capsys.readouterr()

    assert loadout.main(["check"]) == 1
    assert f"overlay {NAME}: DRIFT (orphaned): ~/{root.name}/{LOCAL}" in capsys.readouterr().err

    assert loadout.main(["check", "--staged"]) == 0


SECRET = "q8Zr2LmP0vXy7"


def test_check_withholds_a_claude_json_server_value_that_holds_a_secret(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    servers = folder / "mcp.toml"
    servers.write_text(
        servers.read_text(encoding="utf-8")
        + f'\n[vault]\ntransport = "stdio"\ncommand = "vault-mcp"\n'
        f'env = {{ MY_SERVICE_KEY = "{SECRET}" }}\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME]) == 1

    err = capsys.readouterr().err
    label = f'~/.claude.json#projects["{root}"].mcpServers.vault'
    assert f"overlay {NAME}: DRIFT (missing): {label}\n" in err
    assert f"{label}: 8 line(s) differ; diff not shown because the file holds a secret" in err
    assert SECRET not in err


def test_check_withholds_a_saved_rule_that_holds_a_secret(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    token = "ghp_" + "a1B2c3D4" * 4 + "e5F6"
    path = root / SETTINGS
    document = json.loads(path.read_text(encoding="utf-8"))
    document["permissions"]["allow"].append(f'Bash(curl -H "Authorization: Bearer {token}":*)')
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME, "--json"]) == 0
    out = capsys.readouterr().out
    assert (
        f"overlay {NAME}: ~/overlay-fixture-a: saved by the harness, not in the overlay source: "
        f"{SETTINGS}: permissions.allow: a rule not shown because it holds a secret (a token in "
        "a provider's format); add it to the overlay's permissions.toml or delete it"
    ) in json.loads(out)["notes"]
    assert token not in out

    assert loadout.main(["check", "--overlay", NAME]) == 0
    shown = capsys.readouterr()
    assert "permissions.allow: a rule not shown because it holds a secret" in shown.out
    assert token not in shown.out + shown.err


def test_git_never_runs_the_fsmonitor_a_target_configures(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    marker = fake_home / "fsmonitor-ran"
    _git(root, "config", "core.fsmonitor", f"touch '{marker}'; false")

    assert loadout.main(["check", "--overlay", NAME]) == 1
    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert (root / LOCAL).is_file()
    assert not marker.exists()


def test_git_never_fetches_for_a_tree_a_target_lacks(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    source = _repo(fake_home, "elsewhere")
    _git(root, "fetch", "-q", str(source), "HEAD:refs/heads/other")
    tree = _git(root, "rev-parse", "other^{tree}").strip()
    (root / ".git" / "objects" / tree[:2] / tree[2:]).unlink()
    marker = fake_home / "ssh-ran"
    for key, value in (
        ("core.repositoryformatversion", "1"),
        ("extensions.partialClone", "origin"),
        ("remote.origin.url", "ssh://example.invalid/team.git"),
        ("remote.origin.promisor", "true"),
        ("core.sshCommand", f"touch '{marker}'; false"),
    ):
        _git(root, "config", key, value)
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME]) == 3

    assert "transport 'ssh' not allowed" in capsys.readouterr().err
    assert not marker.exists()


def _ignored_globally(home: Path, ignore: Path) -> None:
    (home / "test-gitconfig").write_text(f"[core]\n\texcludesFile = {ignore}\n", encoding="utf-8")
    _accept_global_ignore(ignore)


@pytest.mark.parametrize("record", ["{broken", '{"version": 99}'])
def test_a_refused_sync_leaves_the_global_ignore_block_in_place(
    fake_home: Path, capsys: pytest.CaptureFixture[str], record: str
) -> None:
    ignore = fake_home / "global-ignore"
    _ignored_globally(fake_home, ignore)
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    block = ignore.read_text(encoding="utf-8")
    assert global_pattern(LOCAL) in block.splitlines()
    assert _git(root, "status", "--porcelain") == ""
    overlay_record_path(folder).write_text(record, encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 3

    assert f"{ignore} is left as it is while an overlay record cannot be read" in (
        capsys.readouterr().err
    )
    assert ignore.read_text(encoding="utf-8") == block
    assert _git(root, "status", "--porcelain") == ""


def test_check_reports_a_deleted_target_as_drift_not_as_a_git_error(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (kept, gone) = _overlay(fake_home, "a", "b")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    shutil.rmtree(gone)
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME, "--json"]) == 1

    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["errors"]) == ("drift", [])
    claude = fake_home / ".claude.json"
    assert sorted((item["path"], item["kind"]) for item in result["drift"]) == [
        (f'{claude}#projects["{gone}"].mcpServers.docs', "orphaned"),
        (f'{claude}#projects["{gone}"].mcpServers.local-tool', "orphaned"),
    ]
    assert kept.is_dir()


def _retarget(folder: Path, *patterns: str) -> None:
    manifest = folder / "loadout.toml"
    targets = ", ".join(f'"{pattern}"' for pattern in patterns)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'targets = ["~/overlay-fixture*"]', f"targets = [{targets}]"
        ),
        encoding="utf-8",
    )


def test_remove_reports_a_target_it_could_not_reach_and_still_says_it_removed(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _overlay(fake_home, "a")
    locked = fake_home / "locked"
    locked.mkdir()
    hidden = _repo(locked, "app")
    _retarget(folder, "~/overlay-fixture*", "~/locked/*")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert (hidden / LOCAL).is_file()
    capsys.readouterr()
    locked.chmod(0)
    try:
        assert loadout.main(["overlay", "remove", NAME, "--yes", "--force"]) == 0
    finally:
        locked.chmod(0o755)

    out = capsys.readouterr().out
    assert (
        f"note: overlay {NAME}: ~/locked/app: cannot be reached; --force drops it from the record "
        "and leaves its outputs in place: "
    ) in out
    assert out.endswith(f"removed overlay {NAME}\n")
    assert (hidden / LOCAL).is_file()


def test_check_refuses_a_unit_a_team_gitignore_re_includes_as_sync_does(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    (root / ".gitignore").write_text(f"!/{LOCAL}\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "commit", "-q", "-m", "team ignore")
    refusal = f"refused: {LOCAL} would show in git status: .gitignore:1:!/{LOCAL} re-includes it"
    capsys.readouterr()

    assert loadout.main(["check", "--overlay", NAME, "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert [(e["target"], e["error"]) for e in result["errors"]] == [(str(root), refusal)]
    assert str(root / LOCAL) not in {item["path"] for item in result["drift"]}

    assert loadout.main(["sync", "--overlay", NAME]) == 3
    assert f"overlay {NAME}: ~/overlay-fixture-a: {refusal}" in capsys.readouterr().err
    assert (root / ".codex" / "config.toml").is_file()


def test_a_sync_stopped_before_a_shared_document_is_written_finishes_on_the_next_run(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(fake_home, "overlay-fixture-a")
    path = root / SETTINGS
    path.parent.mkdir()
    saved = {"permissions": {"allow": ["Bash(npm test:*)"]}}
    path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    _overlay(fake_home)

    def stopped(*_: object) -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(overlay_sync, "_write_document", stopped)
        assert loadout.main(["sync", "--overlay", NAME]) == INTERRUPTED
    assert json.loads(path.read_text(encoding="utf-8")) == saved

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["model"] == "opus"
    assert document["permissions"]["allow"] == [
        "Bash(ls:*)",
        APPROVAL,
        "mcp__docs__search",
        "Bash(npm test:*)",
    ]


def test_a_sync_stopped_before_a_whole_file_is_written_finishes_on_the_next_run(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _edit_source(folder)
    original = overlay_sync.write_outputs

    def stopping(outputs: dict[Path, Any]) -> list[Path]:
        if root / LOCAL in outputs:
            raise KeyboardInterrupt
        return original(outputs)

    with monkeypatch.context() as patch:
        patch.setattr(overlay_sync, "write_outputs", stopping)
        assert loadout.main(["sync", "--overlay", NAME]) == INTERRUPTED
    assert "Edited." not in (root / LOCAL).read_text(encoding="utf-8")

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert "Edited." in (root / LOCAL).read_text(encoding="utf-8")


def test_a_failed_target_still_records_the_overlay_folder_file_it_wrote(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    shared = folder / ".loadout" / "opencode-instructions.md"
    _edit_source(folder)
    root.chmod(0o555)
    try:
        assert loadout.main(["sync", "--overlay", NAME]) == 3
    finally:
        root.chmod(0o755)
    assert "Edited." in shared.read_text(encoding="utf-8")
    _edit_source(folder)
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert shared.read_text(encoding="utf-8").count("Edited.") == 2
    assert (root / LOCAL).read_text(encoding="utf-8").count("Edited.") == 2


def test_a_tracked_orphan_is_left_in_place_and_released_so_the_repository_can_adopt(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (adopting, kept) = _overlay(fake_home, "a", "b")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    _git(adopting, "add", "-f", ".codex/config.toml")
    _git(adopting, "commit", "-q", "-m", "team codex")
    team = (adopting / ".codex" / "config.toml").read_text(encoding="utf-8")
    _retarget(folder, "~/overlay-fixture-b")
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", NAME]) == 0

    assert (
        f"note: overlay {NAME}: ~/overlay-fixture-a: the team tracks .codex/config.toml; it is "
        "left in place and no longer recorded"
    ) in capsys.readouterr().out
    assert (adopting / ".codex" / "config.toml").read_text(encoding="utf-8") == team
    assert sorted(_record(folder)["targets"]) == [str(kept)]
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert loadout.main(["init", "project", "--root", str(adopting), "--dry-run"]) == 0


def test_a_broken_registry_is_an_error_beside_the_project_part(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(fake_home)
    plain = _repo(fake_home, "plain")
    registry = overlays_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("[broken\n", encoding="utf-8")
    monkeypatch.chdir(root)

    assert loadout.main(["check", "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert (result["status"], result["exit_code"]) == ("error", 3)
    assert sorted(item["path"] for item in result["drift"]) == [
        str(root / ".claude" / "settings.json"),
        str(root / "CLAUDE.md"),
    ]
    assert [(e["overlay"], e["target"]) for e in result["errors"]] == [(None, None)]
    assert result["errors"][0]["error"].startswith(str(registry))

    assert loadout.main(["sync"]) == 3
    assert f"loadout: {registry}" in capsys.readouterr().err
    assert (root / "CLAUDE.md").is_file()

    monkeypatch.chdir(plain)
    assert loadout.main(["sync"]) == 3
    err = capsys.readouterr().err
    assert f"loadout: {registry}" in err
    assert f"no manifest found in {plain}" in err


def test_a_second_overlay_is_refused_the_paths_and_servers_the_first_records(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    first = (root / LOCAL).read_text(encoding="utf-8")
    second = fake_home / "overlays" / "second"
    shutil.copytree(OVERLAY_FIXTURES, second)
    (second / "instructions" / "personal.md").write_text("Second.\n", encoding="utf-8")
    assert loadout.main(["overlay", "add", str(second)]) == 0
    capsys.readouterr()

    assert loadout.main(["sync", "--overlay", "second", "--force"]) == 3

    err = capsys.readouterr().err
    lead = "overlay second: ~/overlay-fixture-a: refused: "
    assert f"{lead}{LOCAL} is written by overlay {NAME}; the overlay does not write it" in err
    assert f"{lead}.claude/skills/probe is written by overlay {NAME}; the overlay does not" in err
    assert f"{lead}MCP server docs is written by overlay {NAME}; the overlay does not" in err
    assert (root / LOCAL).read_text(encoding="utf-8") == first
    servers = _record(folder)["targets"][str(root)]["leaves"][str(fake_home / ".claude.json")]
    assert len(servers) == 2

    assert loadout.main(["overlay", "remove", "second", "--yes"]) == 0
    projects = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))["projects"]
    assert sorted(projects[str(root)]["mcpServers"]) == ["docs", "local-tool"]


def test_a_target_that_adopts_loadout_is_retired_by_the_overlay_before_the_project_syncs(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, (root,) = _overlay(fake_home, "a")
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    assert "instructions" in json.loads((root / "opencode.json").read_text(encoding="utf-8"))
    _adopt(root)
    monkeypatch.chdir(root)
    capsys.readouterr()

    assert loadout.main(["sync"]) == 0

    out = capsys.readouterr().out
    note = f"note: overlay {NAME}: ~/overlay-fixture-a: adopted loadout; the overlay retired its "
    assert f"{note}files here" in out
    assert out.index(note) < out.index("CLAUDE.md")
    assert _files(root) == [
        ".claude/settings.json",
        ".gitignore",
        "AGENTS.md",
        "CLAUDE.md",
        "loadout/config.toml",
        "loadout/instructions/team.md",
    ]
    assert str(root) not in _record(folder)["targets"]
    projects = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))["projects"]
    assert projects[str(root)]["mcpServers"] == {}

    assert loadout.main(["overlay", "remove", NAME, "--yes"]) == 0
    assert (root / "CLAUDE.md").is_file()
    assert loadout.main(["check"]) == 0


def test_a_run_scans_the_overlay_records_as_often_for_three_targets_as_for_one(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, roots = _overlay(fake_home, "a", "b", "c")
    second = fake_home / "overlays" / "second"
    shutil.copytree(OVERLAY_FIXTURES, second)
    write_overlay_record(empty_record(second.resolve(), "second", fake_home.resolve()))
    assert loadout.main(["sync", "--overlay", NAME]) == 0
    scans: list[str] = []
    scan = overlay_record.scan_overlay_records

    def counted() -> overlay_record.RecordScan:
        scans.append("scan")
        return scan()

    monkeypatch.setattr(overlay_record, "scan_overlay_records", counted)

    def scans_for(*command: str) -> int:
        scans.clear()
        assert loadout.main(list(command)) == 0
        return len(scans)

    monkeypatch.chdir(roots[0])
    one = (scans_for("check"), scans_for("sync"))
    monkeypatch.chdir(folder)
    every = (scans_for("check"), scans_for("sync"))
    assert one == every
    assert all(count >= 1 for count in every)


def test_check_and_sync_both_mark_a_user_defined_claude_server_refused(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, (root,) = _overlay(fake_home, "a")
    mine = {"type": "stdio", "command": "mine"}
    (fake_home / ".claude.json").write_text(
        json.dumps({"projects": {str(root): {"mcpServers": {"docs": mine}}}}), encoding="utf-8"
    )
    refusal = "refused: MCP server docs in .claude.json was not written by loadout"

    assert loadout.main(["check", "--overlay", NAME]) == 3
    assert f"overlay {NAME}: ERROR: ~/overlay-fixture-a: {refusal}" in capsys.readouterr().err
    assert loadout.main(["check", "--overlay", NAME, "--json"]) == 3
    errors = json.loads(capsys.readouterr().out)["errors"]
    assert [entry["target"] for entry in errors if entry["error"].startswith(refusal)] == [
        str(root)
    ]
    assert loadout.main(["sync", "--overlay", NAME]) == 3
    assert f"overlay {NAME}: ~/overlay-fixture-a: {refusal}" in capsys.readouterr().err
    projects = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))["projects"]
    assert projects[str(root)]["mcpServers"]["docs"] == mine
