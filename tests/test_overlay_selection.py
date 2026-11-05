from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import loadout
from loadout import cli, overlay_commands
from loadout.errors import LoadoutError, UsageError
from loadout.files import Drift
from loadout.overlay_check import OverlayCheck
from loadout.overlay_commands import (
    Selection,
    cmd_check_selection,
    overlays_targeting,
    refuse_targeted_init,
    resolve_selection,
)
from loadout.overlay_record import TargetRecord, empty_record, write_overlay_record
from loadout.overlay_registry import (
    RegisteredOverlay,
    overlays_path,
    update_registry,
    with_overlay,
)
from registration import register

OVERLAY_BODY = """
[overlay]
targets = ["~/apps/*"]

[[source]]
name = "app"
path = "."

[claude]
"""


def _overlay_folder(parent: Path, name: str = "app", body: str = OVERLAY_BODY) -> Path:
    folder = parent / "overlays" / name
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(body, encoding="utf-8")
    return folder.resolve()


def _registered(parent: Path, name: str = "app", body: str = OVERLAY_BODY) -> RegisteredOverlay:
    overlay = RegisteredOverlay(name, _overlay_folder(parent, name, body))
    update_registry(lambda current: with_overlay(current, overlay))
    return overlay


def _select(root: Path, **overrides: object) -> Selection:
    options: dict[str, object] = {
        "use_global": False,
        "overlay": None,
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


def test_staged_selects_the_project_alone_even_where_an_overlay_targets_it(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = _registered(fake_home)
    root = _project(fake_home)
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: root)
    monkeypatch.setattr(overlay_commands, "overlays_targeting", lambda top: ((overlay, top),))

    unstaged = _select(root)
    staged = _select(root, staged=True)

    assert unstaged.overlays == ((overlay, root),)
    assert staged == Selection(root, "default", None, (), False)


def test_staged_with_an_overlay_is_a_usage_error(fake_home: Path) -> None:
    _registered(fake_home)
    with pytest.raises(UsageError, match="--staged"):
        _select(fake_home, staged=True, overlay="app")


def test_an_overlay_by_name_selects_it_whole_and_passes_force(fake_home: Path) -> None:
    overlay = _registered(fake_home)
    assert _select(fake_home, overlay="app", force=True) == Selection(
        None, None, None, ((overlay, None),), True
    )


def test_an_overlay_with_a_profile_is_a_usage_error(fake_home: Path) -> None:
    _registered(fake_home)
    with pytest.raises(UsageError, match="default profile only"):
        _select(fake_home, overlay="app", profile="work")


def test_an_unknown_overlay_name_lists_the_registered_ones(fake_home: Path) -> None:
    _registered(fake_home, "app")
    _registered(fake_home, "web")
    with pytest.raises(LoadoutError, match="no overlay named nope is registered; registered "):
        _select(fake_home, overlay="nope")
    with pytest.raises(LoadoutError) as raised:
        _select(fake_home, overlay="nope")
    assert str(raised.value).endswith("registered overlays: app, web")


def test_global_without_a_machine_config_still_selects_every_overlay(fake_home: Path) -> None:
    app = _registered(fake_home, "app")
    web = _registered(fake_home, "web")

    selection = _select(fake_home, use_global=True, force=True)

    assert (selection.root, selection.profile) == (None, None)
    assert selection.global_error is not None
    assert "run `loadout init global` first" in selection.global_error
    assert selection.overlays == ((app, None), (web, None))
    assert selection.overlay_force is False


def test_global_force_reaches_the_global_source_but_not_the_overlays(
    fake_home: Path, root: Path
) -> None:
    register(root)
    app = _registered(fake_home)

    selection = _select(fake_home, use_global=True, force=True)

    assert selection == Selection(root, "default", None, ((app, None),), False)


def test_a_registered_overlay_folder_selects_that_overlay(fake_home: Path) -> None:
    overlay = _registered(fake_home)
    assert _select(overlay.folder, force=True) == Selection(
        None, None, None, ((overlay, None),), True
    )


def test_an_unregistered_overlay_folder_points_at_overlay_add(fake_home: Path) -> None:
    folder = _overlay_folder(fake_home)
    with pytest.raises(LoadoutError, match=r"register it with `loadout overlay add "):
        _select(folder)


def test_a_project_root_an_overlay_targets_selects_both(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = _registered(fake_home)
    root = _project(fake_home)
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: root)
    monkeypatch.setattr(overlay_commands, "overlays_targeting", lambda top: ((overlay, top),))

    assert _select(root, force=True) == Selection(root, "default", None, ((overlay, root),), True)


def test_a_project_root_whose_registry_is_broken_selects_the_project_and_the_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(fake_home)
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: root)
    registry = overlays_path()
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("[broken\n", encoding="utf-8")

    selection = _select(root)

    assert (selection.root, selection.profile, selection.overlays) == (root, "default", ())
    assert (selection.registry_error or "").startswith(f"{registry}: ")


def test_a_repository_without_a_manifest_selects_the_overlays_targeting_its_top(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = _registered(fake_home)
    top = (fake_home / "apps" / "a").resolve()
    asked: list[Path] = []
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: top)
    monkeypatch.setattr(
        overlay_commands,
        "overlays_targeting",
        lambda found: asked.append(found) or ((overlay, found),),
    )

    assert _select(fake_home / "apps" / "a" / "src") == Selection(
        None, None, None, ((overlay, top),), False
    )
    assert asked == [top]


def test_overlays_alone_with_a_profile_is_a_usage_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = _registered(fake_home)
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: path)
    monkeypatch.setattr(overlay_commands, "overlays_targeting", lambda top: ((overlay, top),))
    with pytest.raises(UsageError, match="default profile only"):
        _select(fake_home, profile="work")


def test_no_manifest_and_no_overlay_names_both_in_the_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _registered(fake_home)
    top = fake_home.resolve()
    monkeypatch.setattr(overlay_commands, "toplevel", lambda path: top)
    with pytest.raises(LoadoutError) as raised:
        _select(fake_home / "sub")
    assert str(raised.value) == (
        f"no manifest found in {fake_home / 'sub'}: expected loadout.toml or "
        f"loadout/config.toml, and no registered overlay targets {top}"
    )


def test_targeting_matches_by_pattern_and_warns_for_an_overlay_that_cannot_load(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    app = _registered(fake_home, "app")
    _registered(fake_home, "broken", body="[overlay]\ntargets = []\n")
    _registered(fake_home, "other", body=OVERLAY_BODY.replace("~/apps/*", "~/elsewhere/*"))
    (fake_home / "apps" / "a").mkdir(parents=True)
    target = (fake_home / "apps" / "a").resolve()

    assert overlays_targeting(target) == ((app, target),)
    assert "WARNING: overlay broken: " in capsys.readouterr().err


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


def test_init_project_is_refused_in_a_repository_an_overlay_record_targets(
    fake_home: Path,
) -> None:
    folder = _overlay_folder(fake_home)
    target = fake_home / "apps" / "a"
    target.mkdir(parents=True)
    record = empty_record(folder, "app", fake_home)
    write_overlay_record(replace(record, targets={target.resolve(): _entry(target.resolve())}))

    with pytest.raises(LoadoutError) as raised:
        refuse_targeted_init(target)
    assert str(raised.value) == (
        "an overlay writes personal files here; adopting would sweep them into the team's "
        "source — remove this repository from overlay app first: narrow its `targets`, then run "
        "`loadout sync --overlay app`"
    )
    assert loadout.main(["init", "project", "--root", str(target), "--dry-run"]) == 3


def _check(
    name: str, drift: tuple[Drift, ...], errors: tuple[tuple[Path | None, str], ...], code: int
) -> OverlayCheck:
    return OverlayCheck(name, drift, (f"{name} note",), errors, code)


def test_check_json_prints_one_object_holding_every_part(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fake_home / "project"
    app = RegisteredOverlay("app", fake_home / "app")
    web = RegisteredOverlay("web", fake_home / "web")
    target = fake_home / "apps" / "a"
    checks = {
        "app": _check(
            "app",
            (Drift(path=target / "CLAUDE.local.md", actual="a", expected="b", kind="hand-edited"),),
            ((target, "refused: the team tracks opencode.json"),),
            3,
        ),
        "web": _check("web", (), ((None, "the overlay record x cannot be read"),), 3),
    }
    project = {
        "status": "drift",
        "exit_code": 1,
        "drift": [{"path": str(root / "AGENTS.md"), "kind": "missing"}],
        "notes": ["project note"],
    }
    monkeypatch.setattr(overlay_commands, "check_result", lambda found, profile: project)
    monkeypatch.setattr(
        overlay_commands, "check_overlay", lambda overlay, scope: checks[overlay.name]
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
        "notes": ["project note", "overlay app: app note", "overlay web: web note"],
        "errors": [
            {
                "overlay": "app",
                "target": str(target),
                "error": "refused: the team tracks opencode.json",
            },
            {"overlay": "web", "target": None, "error": "the overlay record x cannot be read"},
        ],
    }


def test_check_json_reports_a_failing_root_part_inside_the_object(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = fake_home / "project"
    app = RegisteredOverlay("app", fake_home / "app")

    def broken(found: Path, profile: str) -> dict[str, object]:
        raise LoadoutError("the source does not parse")

    monkeypatch.setattr(overlay_commands, "check_result", broken)
    monkeypatch.setattr(
        overlay_commands, "check_overlay", lambda overlay, scope: _check("app", (), (), 0)
    )
    selection = Selection(root, "default", "no machine config", ((app, None),), False)

    assert cmd_check_selection(selection, as_json=True) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "status": "error",
        "exit_code": 3,
        "drift": [],
        "notes": ["overlay app: app note"],
        "errors": [
            {"overlay": None, "target": None, "error": "no machine config"},
            {"overlay": None, "target": str(root), "error": "the source does not parse"},
        ],
    }


def test_a_plain_folder_without_overlays_gets_the_old_error_and_runs_no_git(
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

    assert overlays_path().exists() is False
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

    monkeypatch.setattr(overlay_commands, "cmd_sync", recording)

    assert loadout.main(["sync", "--global", "--profile", "work"]) == 0
    assert synced == [(source, "work-chosen")]
