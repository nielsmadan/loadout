from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import loadout
from loadout import cli, kit_fanout, stealth_commands
from loadout.claude_json import server_path
from loadout.errors import LoadoutError, UsageError
from loadout.files import Drift
from loadout.stealth_check import StealthCheck
from loadout.stealth_commands import (
    Selection,
    cmd_check_selection,
    resolve_selection,
    stealth_targeting,
    stealth_written_files,
)
from loadout.stealth_record import TargetRecord, empty_record, write_stealth_record
from loadout.stealth_registry import (
    RegisteredStealth,
    stealth_path,
    update_registry,
    with_stealth,
)
from loadout.written import WrittenEntry
from registration import register

STEALTH_BODY = """
[stealth]
targets = ["~/apps/*"]

[[source]]
name = "app"
path = "."

[claude]
"""


def _stealth_folder(parent: Path, name: str = "app", body: str = STEALTH_BODY) -> Path:
    folder = parent / "stealth" / name
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(body, encoding="utf-8")
    return folder.resolve()


def _registered(parent: Path, name: str = "app", body: str = STEALTH_BODY) -> RegisteredStealth:
    stealth = RegisteredStealth(name, _stealth_folder(parent, name, body))
    update_registry(lambda current: with_stealth(current, stealth))
    return stealth


def _select(root: Path, **overrides: object) -> Selection:
    options: dict[str, object] = {
        "use_global": False,
        "stealth": None,
        "profile": None,
        "force": False,
        "staged": False,
        **overrides,
    }
    args = argparse.Namespace(
        root=root, use_global=options["use_global"], profile=options["profile"]
    )
    return resolve_selection(  # type: ignore[arg-type]
        root, **options, resolve=lambda: cli._resolve_root_and_profile(args)
    )


def _project(parent: Path) -> Path:
    root = parent / "project"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout" / "config.toml").write_text('harnesses = ["claude"]\n', encoding="utf-8")
    return root.resolve()


def test_staged_selects_the_project_alone_even_where_a_stealth_source_targets_it(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stealth = _registered(fake_home)
    root = _project(fake_home)
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: root)
    monkeypatch.setattr(stealth_commands, "stealth_targeting", lambda top: ((stealth, top),))

    unstaged = _select(root)
    staged = _select(root, staged=True)

    assert unstaged.stealth_sources == ((stealth, root),)
    assert staged == Selection(root, "default", None, (), False)


def test_staged_with_a_stealth_source_is_a_usage_error(fake_home: Path) -> None:
    _registered(fake_home)
    with pytest.raises(UsageError, match="--staged"):
        _select(fake_home, staged=True, stealth="app")


def test_a_stealth_source_by_name_selects_it_whole_and_passes_force(fake_home: Path) -> None:
    stealth = _registered(fake_home)
    assert _select(fake_home, stealth="app", force=True) == Selection(
        None, None, None, ((stealth, None),), True
    )


def test_a_stealth_source_with_a_profile_is_a_usage_error(fake_home: Path) -> None:
    _registered(fake_home)
    with pytest.raises(UsageError, match="default profile only"):
        _select(fake_home, stealth="app", profile="work")


def test_an_unknown_stealth_name_lists_the_registered_ones(fake_home: Path) -> None:
    _registered(fake_home, "app")
    _registered(fake_home, "web")
    with pytest.raises(
        LoadoutError, match="no stealth source named nope is registered; registered "
    ):
        _select(fake_home, stealth="nope")
    with pytest.raises(LoadoutError) as raised:
        _select(fake_home, stealth="nope")
    assert str(raised.value).endswith("registered stealth sources: app, web")


def test_global_without_a_machine_config_still_selects_every_stealth_source(
    fake_home: Path,
) -> None:
    app = _registered(fake_home, "app")
    web = _registered(fake_home, "web")

    selection = _select(fake_home, use_global=True, force=True)

    assert (selection.root, selection.profile) == (None, None)
    assert selection.global_error is not None
    assert "run `loadout init global` first" in selection.global_error
    assert selection.stealth_sources == ((app, None), (web, None))
    assert selection.stealth_force is False


def test_global_force_reaches_the_global_source_but_not_the_stealth_sources(
    fake_home: Path, root: Path
) -> None:
    register(root)
    app = _registered(fake_home)

    selection = _select(fake_home, use_global=True, force=True)

    assert selection == Selection(root, "default", None, ((app, None),), False, fan_out=True)


def test_a_registered_stealth_folder_selects_that_stealth_source(fake_home: Path) -> None:
    stealth = _registered(fake_home)
    assert _select(stealth.folder, force=True) == Selection(
        None, None, None, ((stealth, None),), True
    )


def test_an_unregistered_stealth_folder_points_at_stealth_source_add(fake_home: Path) -> None:
    folder = _stealth_folder(fake_home)
    with pytest.raises(LoadoutError, match=r"register it with `loadout stealth add "):
        _select(folder)


def test_a_project_root_a_stealth_source_targets_selects_both(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stealth = _registered(fake_home)
    root = _project(fake_home)
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: root)
    monkeypatch.setattr(stealth_commands, "stealth_targeting", lambda top: ((stealth, top),))

    assert _select(root, force=True) == Selection(root, "default", None, ((stealth, root),), True)


def test_a_project_root_whose_registry_is_broken_selects_the_project_and_the_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(fake_home)
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: root)
    registry = stealth_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("[broken\n", encoding="utf-8")

    selection = _select(root)

    assert (selection.root, selection.profile, selection.stealth_sources) == (root, "default", ())
    assert (selection.registry_error or "").startswith(f"{registry}: ")


def test_a_repository_without_a_manifest_selects_the_stealth_sources_targeting_its_top(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stealth = _registered(fake_home)
    top = (fake_home / "apps" / "a").resolve()
    asked: list[Path] = []
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: top)
    monkeypatch.setattr(
        stealth_commands,
        "stealth_targeting",
        lambda found: asked.append(found) or ((stealth, found),),
    )

    assert _select(fake_home / "apps" / "a" / "src") == Selection(
        None, None, None, ((stealth, top),), False
    )
    assert asked == [top]


def test_stealth_sources_alone_with_a_profile_is_a_usage_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stealth = _registered(fake_home)
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: path)
    monkeypatch.setattr(stealth_commands, "stealth_targeting", lambda top: ((stealth, top),))
    with pytest.raises(UsageError, match="default profile only"):
        _select(fake_home, profile="work")


def test_no_manifest_and_no_stealth_source_names_both_in_the_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(fake_home)
    top = fake_home.resolve()
    monkeypatch.setattr(stealth_commands, "toplevel", lambda path: top)
    with pytest.raises(LoadoutError) as raised:
        _select(fake_home / "sub")
    assert str(raised.value) == (
        f"no manifest found in {fake_home / 'sub'}: expected loadout.toml or "
        f"loadout/config.toml, and no registered stealth source targets {top}"
    )


def test_targeting_matches_by_pattern_and_warns_for_a_stealth_source_that_cannot_load(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _registered(fake_home, "app")
    _registered(fake_home, "broken", body="[stealth]\ntargets = []\n")
    _registered(fake_home, "other", body=STEALTH_BODY.replace("~/apps/*", "~/elsewhere/*"))
    (fake_home / "apps" / "a").mkdir(parents=True)
    target = (fake_home / "apps" / "a").resolve()

    assert stealth_targeting(target) == ((app, target),)
    assert "WARNING: stealth broken: " in capsys.readouterr().err


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


def test_the_files_a_stealth_record_lists_for_a_repository_are_named_with_their_source(
    fake_home: Path,
) -> None:
    folder = _stealth_folder(fake_home)
    target = fake_home / "apps" / "a"
    target.mkdir(parents=True)
    entry = replace(
        _entry(target.resolve()),
        files={target.resolve() / "CLAUDE.local.md": WrittenEntry(kind="copied", sha256="c0")},
        leaves={target.resolve() / ".claude.json": {server_path(target.resolve(), "figma"): "l1"}},
    )
    record = empty_record(folder, "app", fake_home)
    write_stealth_record(
        replace(
            record,
            targets={
                target.resolve(): entry,
            },
        )
    )

    assert stealth_written_files(target) == ((target.resolve() / "CLAUDE.local.md", "app"),)


def _check(
    name: str, drift: tuple[Drift, ...], errors: tuple[tuple[Path | None, str], ...], code: int
) -> StealthCheck:
    return StealthCheck(name, drift, (f"{name} note",), errors, code)


def test_check_json_prints_one_object_holding_every_part(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fake_home / "project"
    app = RegisteredStealth("app", fake_home / "app")
    web = RegisteredStealth("web", fake_home / "web")
    target = fake_home / "apps" / "a"
    checks = {
        "app": _check(
            "app",
            (Drift(path=target / "CLAUDE.local.md", actual="a", expected="b", kind="hand-edited"),),
            ((target, "refused: the team tracks opencode.json"),),
            3,
        ),
        "web": _check("web", (), ((None, "the stealth record x cannot be read"),), 3),
    }
    project = {
        "status": "drift",
        "exit_code": 1,
        "drift": [{"path": str(root / "AGENTS.md"), "kind": "missing"}],
        "notes": ["project note"],
    }
    monkeypatch.setattr(stealth_commands, "check_result", lambda found, profile: project)
    monkeypatch.setattr(
        stealth_commands, "check_stealth", lambda stealth, scope: checks[stealth.name]
    )
    selection = Selection(root, "default", None, ((app, target), (web, None)), False)

    code = cmd_check_selection(selection, as_json=True)

    assert code == 3
    assert json.loads(capsys.readouterr().out) == {
        "status": "error",
        "exit_code": 3,
        "drift": [
            {"path": str(root / "AGENTS.md"), "kind": "missing"},
            {"path": str(target / "CLAUDE.local.md"), "kind": "hand-edited"},
        ],
        "notes": ["project note", "stealth app: app note", "stealth web: web note"],
        "errors": [
            {
                "stealth": "app",
                "target": str(target),
                "error": "refused: the team tracks opencode.json",
            },
            {"stealth": "web", "target": None, "error": "the stealth record x cannot be read"},
        ],
    }


def test_check_json_reports_a_failing_root_part_inside_the_object(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fake_home / "project"
    app = RegisteredStealth("app", fake_home / "app")

    def broken(found: Path, profile: str) -> dict[str, object]:
        raise LoadoutError("the source does not parse")

    monkeypatch.setattr(stealth_commands, "check_result", broken)
    monkeypatch.setattr(
        stealth_commands, "check_stealth", lambda stealth, scope: _check("app", (), (), 0)
    )
    selection = Selection(root, "default", "no machine config", ((app, None),), False)

    assert cmd_check_selection(selection, as_json=True) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "status": "error",
        "exit_code": 3,
        "drift": [],
        "notes": ["stealth app: app note"],
        "errors": [
            {"stealth": None, "target": None, "error": "no machine config"},
            {"stealth": None, "target": str(root), "error": "the source does not parse"},
        ],
    }


def test_a_plain_folder_without_stealth_sources_gets_the_old_error_and_runs_no_git(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain = fake_home / "plain"
    plain.mkdir()
    launched: list[str] = []
    real_popen = subprocess.Popen

    def recording(args: Any, *rest: Any, **options: Any) -> Any:
        launched.append(Path(str(args[0])).name)
        return real_popen(args, *rest, **options)

    monkeypatch.setattr(subprocess, "Popen", recording)
    message = (
        f"loadout: no manifest found in {plain.resolve()}: expected loadout.toml or "
        "loadout/config.toml\n"
    )

    assert stealth_path().exists() is False
    for command in ("sync", "check"):
        assert loadout.main([command, "--root", str(plain)]) == 3
        assert capsys.readouterr().err == message
    assert launched == []


def test_global_selection_resolves_through_the_cli_resolver(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = fake_home / "source"
    synced: list[tuple[Path, str]] = []
    monkeypatch.setattr(cli, "_resolve_global", lambda profile: (source, f"{profile}-chosen"))

    def recording(root: Path, *, profile: str, force: bool) -> int:
        synced.append((root, profile))
        return 0

    monkeypatch.setattr(kit_fanout, "cmd_sync", recording)

    assert loadout.main(["sync", "--global", "--profile", "work"]) == 0
    assert synced == [(source, "work-chosen")]


def _fan_out(
    monkeypatch: pytest.MonkeyPatch, root: Path, kit: tuple[Path, ...]
) -> dict[str, list[tuple[Path, ...]]]:
    clones = [root.parent / name for name in ("one", "two", "three")]
    reached: dict[str, list[tuple[Path, ...]]] = {"sync": [], "check": []}
    monkeypatch.setattr(stealth_commands, "adopted_roots", lambda stealth: (root, *clones[:2]))
    monkeypatch.setattr(stealth_commands, "roots_naming_stealth", lambda name: clones[1:])
    monkeypatch.setattr(stealth_commands, "adopted_target", lambda found: True)
    monkeypatch.setattr(stealth_commands, "_projects", lambda selection: kit)
    monkeypatch.setattr(stealth_commands, "_sync_one", lambda stealth, scope, force: 0)
    monkeypatch.setattr(stealth_commands, "_root_part", lambda selection, action: 0)
    monkeypatch.setattr(stealth_commands, "sync_projects", lambda roots: 0)
    monkeypatch.setattr(stealth_commands, "check_projects", lambda roots: 0)
    monkeypatch.setattr(stealth_commands, "_report_one", lambda check: 0)
    monkeypatch.setattr(stealth_commands, "_checked", lambda stealth, scope: None)
    monkeypatch.setattr(
        stealth_commands, "sync_plain", lambda roots: reached["sync"].append(tuple(roots)) or 0
    )
    monkeypatch.setattr(
        stealth_commands, "check_plain", lambda roots: reached["check"].append(tuple(roots)) or 0
    )
    return reached


def test_an_unscoped_stealth_run_reaches_its_adopted_clones_but_not_the_root_or_kit_projects(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_home / "project"
    kit = (fake_home / "three",)
    reached = _fan_out(monkeypatch, root, kit)
    app = RegisteredStealth("app", fake_home / "app")
    selection = Selection(root, "default", None, ((app, None),), False)

    assert stealth_commands.cmd_sync_selection(selection, force=False) == 0
    assert cmd_check_selection(selection, as_json=False) == 0

    clones = (fake_home / "one", fake_home / "two")
    assert reached == {"sync": [clones], "check": [clones]}


def test_a_stealth_run_scoped_to_one_target_reaches_no_other_clone(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = fake_home / "project"
    reached = _fan_out(monkeypatch, root, ())
    app = RegisteredStealth("app", fake_home / "app")
    selection = Selection(root, "default", None, ((app, root),), False)

    assert stealth_commands.cmd_sync_selection(selection, force=False) == 0

    assert reached["sync"] == [()]
