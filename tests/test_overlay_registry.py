from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout import machine
from loadout.errors import LoadoutError
from loadout.overlay_registry import (
    RegisteredOverlay,
    load_registry,
    marked_removing,
    overlays_path,
    parse_registry,
    render_registry,
    with_overlay,
    without_overlay,
)

HEAD = """
[overlay]
targets = ["~/wrksp/app*", "~/other"]

[[source]]
name = "app"
path = "."

[claude]
"""


def make_overlay(parent: Path, name: str = "App", body: str = HEAD) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(body, encoding="utf-8")
    return folder


def test_rendering_then_loading_round_trips_and_matches_the_file_shape(tmp_path: Path) -> None:
    overlays = (
        RegisteredOverlay("zed", Path("/z"), removing=True),
        RegisteredOverlay("app", Path("/a")),
    )
    text = render_registry(overlays)
    assert text == (
        "# Overlays registered on this machine, managed by `loadout overlay add` and\n"
        "# `loadout overlay remove`.\n"
        "[overlay.app]\n"
        'path = "/a"\n'
        "\n"
        "[overlay.zed]\n"
        'path = "/z"\n'
        "removing = true\n"
    )
    file = tmp_path / "overlays.toml"
    file.write_text(text, encoding="utf-8")
    assert load_registry(file) == tuple(sorted(overlays, key=lambda o: o.name))


def test_an_absent_file_registers_nothing(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "overlays.toml") == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[overlay.app]\npath = "/a"\ncolor = 1\n', "unknown key 'color'"),
        ('[overlay.app]\npath = "a/b"\n', "absolute"),
        ('[overlay.App]\npath = "/a"\n', "must match"),
        ('[overlay.a]\npath = "/x"\n[overlay.b]\npath = "/x"\n', "more than once"),
        ("[other]\nx = 1\n", "unknown key 'other'"),
    ],
)
def test_a_malformed_registry_is_refused_naming_the_file(
    tmp_path: Path, text: str, message: str
) -> None:
    file = tmp_path / "overlays.toml"
    file.write_text(text, encoding="utf-8")
    with pytest.raises(LoadoutError) as raised:
        load_registry(file)
    assert str(file) in str(raised.value)
    assert message in str(raised.value)


def test_a_duplicate_name_or_folder_is_refused_and_the_rest_is_kept() -> None:
    current = (RegisteredOverlay("app", Path("/a")),)
    with pytest.raises(LoadoutError, match="already registered"):
        with_overlay(current, RegisteredOverlay("app", Path("/b")))
    with pytest.raises(LoadoutError, match="already registered as the overlay app"):
        with_overlay(current, RegisteredOverlay("other", Path("/a")))
    both = with_overlay(current, RegisteredOverlay("b", Path("/b")))
    assert [o.name for o in both] == ["app", "b"]
    assert [o.name for o in without_overlay(both, "app")] == ["b"]
    assert [o.removing for o in marked_removing(both, "b")] == [False, True]


def test_the_text_parser_names_the_path_it_was_given() -> None:
    with pytest.raises(LoadoutError, match=r"/x/overlays[.]toml"):
        parse_registry("not toml [", Path("/x/overlays.toml"))


def test_add_registers_in_the_machine_config_and_list_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    assert loadout.main(["overlay", "add", str(folder)]) == 0
    assert load_registry(overlays_path()) == (RegisteredOverlay("app", folder.resolve()),)
    assert overlays_path() == machine.config_dir() / "overlays.toml"
    added = capsys.readouterr().out
    assert f"registered overlay app at {folder.resolve()}" in added
    assert "loadout check --overlay app" in added

    assert loadout.main(["overlay", "list"]) == 0
    assert capsys.readouterr().out == f"app  {folder.resolve()}  ~/wrksp/app*, ~/other\n"


def test_list_with_nothing_registered_says_how_to_add(capsys: pytest.CaptureFixture[str]) -> None:
    assert loadout.main(["overlay", "list"]) == 0
    assert "no overlays registered; add one with `loadout overlay add <folder>`" in (
        capsys.readouterr().out
    )


def test_add_refuses_a_second_registration_of_the_same_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    assert loadout.main(["overlay", "add", str(folder)]) == 0
    capsys.readouterr()
    assert loadout.main(["overlay", "add", str(folder), "--name", "again"]) == 3
    assert "already registered as the overlay app" in capsys.readouterr().err
    other = make_overlay(tmp_path, "Elsewhere")
    assert loadout.main(["overlay", "add", str(other), "--name", "app"]) == 3
    assert "already registered" in capsys.readouterr().err


def test_add_refuses_a_name_that_does_not_fit_and_says_to_pass_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path, "My_App")
    assert loadout.main(["overlay", "add", str(folder)]) == 3
    assert "pass --name" in capsys.readouterr().err
    assert not overlays_path().exists()


def test_add_of_a_disallowed_slice_exits_3_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path, body=HEAD + "hooks = []\n")
    assert loadout.main(["overlay", "add", str(folder)]) == 3
    assert "claude.hooks" in capsys.readouterr().err
    assert not overlays_path().exists()


def test_add_refuses_the_registered_global_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    machine.machine_config_path().parent.mkdir(parents=True)
    machine.machine_config_path().write_text(f'source = "{folder}"\n', encoding="utf-8")
    assert loadout.main(["overlay", "add", str(folder)]) == 3
    assert "global source" in capsys.readouterr().err


def test_add_refuses_a_folder_an_unregistered_record_holds_under_another_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    records = machine.config_dir() / "overlay-written"
    records.mkdir(parents=True)
    (records / "x.json").write_text(
        f'{{"version": 1, "overlay": "{folder.resolve()}", "name": "old"}}', encoding="utf-8"
    )
    assert loadout.main(["overlay", "add", str(folder)]) == 3
    assert "loadout overlay remove old" in capsys.readouterr().err


def test_add_ignores_a_record_whose_name_is_registered_elsewhere(tmp_path: Path) -> None:
    moved = make_overlay(tmp_path, "Moved")
    folder = make_overlay(tmp_path)
    assert loadout.main(["overlay", "add", str(moved), "--name", "old"]) == 0
    records = machine.config_dir() / "overlay-written"
    records.mkdir(parents=True)
    (records / "x.json").write_text(
        f'{{"version": 1, "overlay": "{folder.resolve()}", "name": "old"}}', encoding="utf-8"
    )
    assert loadout.main(["overlay", "add", str(folder), "--name", "x"]) == 0
    assert [o.name for o in load_registry(overlays_path())] == ["old", "x"]


def test_add_reports_an_unusable_state_directory_with_code_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    blocker = tmp_path / "state-is-a-file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert loadout.main(["overlay", "add", str(folder)]) == 3
    assert "overlay-locks" in capsys.readouterr().err
    assert not overlays_path().exists()


def test_list_shows_why_an_overlay_cannot_load_and_when_it_is_being_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = tmp_path / "gone"
    busy = tmp_path / "busy"
    overlays_path().parent.mkdir(parents=True)
    overlays_path().write_text(
        render_registry(
            [RegisteredOverlay("gone", gone), RegisteredOverlay("busy", busy, removing=True)]
        ),
        encoding="utf-8",
    )
    assert loadout.main(["overlay", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"busy  {busy}  (removing: run `loadout overlay remove busy`)"
    assert lines[1].startswith(f"gone  {gone}  (cannot load: ")


def test_an_unregistered_overlay_folder_is_pointed_at_overlay_add(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(folder)]) == 3
    assert f"holds an overlay; register it with `loadout overlay add {folder}`" in (
        capsys.readouterr().err
    )


def test_a_registered_overlay_folder_is_pointed_at_sync_overlay(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_overlay(tmp_path)
    assert loadout.main(["overlay", "add", str(folder)]) == 0
    capsys.readouterr()
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(folder)]) == 3
    err = capsys.readouterr().err
    assert "is the registered overlay app" in err
    assert "`loadout sync --overlay app`" in err


ADDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "from loadout.overlay_registry import RegisteredOverlay, update_registry, with_overlay\n"
    "name = sys.argv[1]\n"
    "for i in range(10):\n"
    "    update_registry(lambda cur: with_overlay(cur, RegisteredOverlay(f'{name}{i}', Path(f'/{name}/{i}'))))\n"
)


def test_concurrent_updates_each_keep_their_own_entries() -> None:
    children = [subprocess.Popen([sys.executable, "-c", ADDER, name]) for name in ("a", "b")]
    assert [child.wait(timeout=60) for child in children] == [0, 0]
    names = [o.name for o in load_registry(overlays_path())]
    assert names == sorted([f"{n}{i}" for n in "ab" for i in range(10)])
