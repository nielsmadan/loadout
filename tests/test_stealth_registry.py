from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout import machine
from loadout.errors import LoadoutError
from loadout.stealth_record import (
    NEWER_RECORD,
    empty_record,
    stealth_record_path,
    write_stealth_record,
)
from loadout.stealth_registry import (
    RegisteredStealth,
    find_by_folder,
    load_registry,
    marked_removing,
    parse_registry,
    render_registry,
    stealth_path,
    with_stealth,
    without_stealth,
)

HEAD = """
[stealth]
targets = ["~/wrksp/app*", "~/other"]

[[source]]
name = "app"
path = "."

[claude]
"""


def make_stealth(parent: Path, name: str = "App", body: str = HEAD) -> Path:
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(body, encoding="utf-8")
    return folder


def test_rendering_then_loading_round_trips_and_matches_the_file_shape(tmp_path: Path) -> None:
    stealth_sources = (
        RegisteredStealth("zed", Path("/z"), removing=True),
        RegisteredStealth("app", Path("/a")),
    )
    text = render_registry(stealth_sources)
    assert text == (
        "# Stealth sources registered on this machine, managed by `loadout stealth add` and\n"
        "# `loadout stealth remove`.\n"
        "[stealth.app]\n"
        'path = "/a"\n'
        "\n"
        "[stealth.zed]\n"
        'path = "/z"\n'
        "removing = true\n"
    )
    file = tmp_path / "stealth.toml"
    file.write_text(text, encoding="utf-8")
    assert load_registry(file) == tuple(sorted(stealth_sources, key=lambda o: o.name))


def test_an_absent_file_registers_nothing(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "stealth.toml") == ()


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('[stealth.app]\npath = "/a"\ncolor = 1\n', "unknown key 'color'"),
        ('[stealth.app]\npath = "a/b"\n', "absolute"),
        ('[stealth.App]\npath = "/a"\n', "must match"),
        ('[stealth.a]\npath = "/x"\n[stealth.b]\npath = "/x"\n', "more than once"),
        ("[other]\nx = 1\n", "unknown key 'other'"),
    ],
)
def test_a_malformed_registry_is_refused_naming_the_file(
    tmp_path: Path, text: str, message: str
) -> None:
    file = tmp_path / "stealth.toml"
    file.write_text(text, encoding="utf-8")
    with pytest.raises(LoadoutError) as raised:
        load_registry(file)
    assert str(file) in str(raised.value)
    assert message in str(raised.value)


def test_a_duplicate_name_or_folder_is_refused_and_the_rest_is_kept() -> None:
    current = (RegisteredStealth("app", Path("/a")),)
    with pytest.raises(LoadoutError, match="already registered"):
        with_stealth(current, RegisteredStealth("app", Path("/b")))
    with pytest.raises(LoadoutError, match="already registered as the stealth source app"):
        with_stealth(current, RegisteredStealth("other", Path("/a")))
    both = with_stealth(current, RegisteredStealth("b", Path("/b")))
    assert [o.name for o in both] == ["app", "b"]
    assert [o.name for o in without_stealth(both, "app")] == ["b"]
    assert [o.removing for o in marked_removing(both, "b")] == [False, True]


def test_the_text_parser_names_the_path_it_was_given() -> None:
    with pytest.raises(LoadoutError, match=r"/x/stealth[.]toml"):
        parse_registry("not toml [", Path("/x/stealth.toml"))


def test_add_registers_in_the_machine_config_and_list_prints_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    assert load_registry(stealth_path()) == (RegisteredStealth("app", folder.resolve()),)
    assert stealth_path() == machine.config_dir() / "stealth.toml"
    added = capsys.readouterr().out
    assert f"registered stealth source app at {folder.resolve()}" in added
    assert "loadout check --stealth app" in added

    assert loadout.main(["stealth", "list"]) == 0
    assert capsys.readouterr().out == f"app  {folder.resolve()}  ~/wrksp/app*, ~/other\n"


def test_list_with_nothing_registered_says_how_to_add(capsys: pytest.CaptureFixture[str]) -> None:
    assert loadout.main(["stealth", "list"]) == 0
    assert "no stealth sources registered; add one with `loadout stealth add <folder>`" in (
        capsys.readouterr().out
    )


def test_add_refuses_a_second_registration_of_the_same_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    capsys.readouterr()
    assert loadout.main(["stealth", "add", str(folder), "--name", "again"]) == 3
    assert "already registered as the stealth source app" in capsys.readouterr().err
    other = make_stealth(tmp_path, "Elsewhere")
    assert loadout.main(["stealth", "add", str(other), "--name", "app"]) == 3
    assert "already registered" in capsys.readouterr().err


def test_add_refuses_the_same_folder_under_another_case_spelling(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path, "OV")
    respelled = tmp_path / "ov"
    if not respelled.exists():
        pytest.skip("the temporary file system is case-sensitive")
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    capsys.readouterr()

    assert loadout.main(["stealth", "add", str(respelled), "--name", "ov2"]) == 3

    assert "already registered as the stealth source ov" in capsys.readouterr().err
    assert load_registry(stealth_path()) == (RegisteredStealth("ov", folder.resolve()),)
    assert find_by_folder(load_registry(stealth_path()), respelled.resolve()) == (
        RegisteredStealth("ov", folder.resolve())
    )


def test_add_refuses_a_name_that_does_not_fit_and_says_to_pass_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path, "My_App")
    assert loadout.main(["stealth", "add", str(folder)]) == 2
    assert "pass --name" in capsys.readouterr().err
    assert not stealth_path().exists()


def test_add_of_a_disallowed_slice_exits_3_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path, body=HEAD + "hooks = []\n")
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    assert "claude.hooks" in capsys.readouterr().err
    assert not stealth_path().exists()


def test_add_refuses_the_registered_global_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    machine.machine_config_path().parent.mkdir(parents=True)
    machine.machine_config_path().write_text(f'source = "{folder}"\n', encoding="utf-8")
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    assert "global source" in capsys.readouterr().err


def test_add_refuses_a_folder_an_unregistered_record_holds_under_another_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    write_stealth_record(empty_record(folder.resolve(), "old", tmp_path))
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    assert "loadout stealth remove old" in capsys.readouterr().err


def test_add_refuses_while_a_partial_record_holds_the_folder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    path = stealth_record_path(folder.resolve())
    path.parent.mkdir(parents=True)
    path.write_text(
        f'{{"version": 2, "stealth": "{folder.resolve()}", "name": "old"}}', encoding="utf-8"
    )
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    err = capsys.readouterr().err
    assert str(path) in err
    assert "loadout stealth remove old --force" in err
    assert not stealth_path().exists()


def test_add_refuses_while_another_folder_s_record_is_from_a_newer_loadout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = make_stealth(tmp_path, "Other")
    folder = make_stealth(tmp_path)
    write_stealth_record(empty_record(other.resolve(), "other", tmp_path))
    path = stealth_record_path(other.resolve())
    path.write_text(path.read_text(encoding="utf-8").replace('"version": 2', '"version": 3'))
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    err = capsys.readouterr().err
    assert f"{path} {NEWER_RECORD}" in err
    assert "loadout stealth remove other --force" in err
    assert not stealth_path().exists()


def test_add_ignores_a_record_whose_name_is_registered_elsewhere(tmp_path: Path) -> None:
    moved = make_stealth(tmp_path, "Moved")
    folder = make_stealth(tmp_path)
    assert loadout.main(["stealth", "add", str(moved), "--name", "old"]) == 0
    write_stealth_record(empty_record(folder.resolve(), "old", tmp_path))
    assert loadout.main(["stealth", "add", str(folder), "--name", "x"]) == 0
    assert [o.name for o in load_registry(stealth_path())] == ["old", "x"]


def test_add_reports_an_unusable_state_directory_with_code_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    blocker = tmp_path / "state-is-a-file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))
    assert loadout.main(["stealth", "add", str(folder)]) == 3
    assert "stealth-locks" in capsys.readouterr().err
    assert not stealth_path().exists()


def test_list_shows_why_a_stealth_source_cannot_load_and_when_it_is_being_removed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    gone = tmp_path / "gone"
    busy = tmp_path / "busy"
    stealth_path().parent.mkdir(parents=True)
    stealth_path().write_text(
        render_registry(
            [RegisteredStealth("gone", gone), RegisteredStealth("busy", busy, removing=True)]
        ),
        encoding="utf-8",
    )
    assert loadout.main(["stealth", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"busy  {busy}  (removing: run `loadout stealth remove busy`)"
    assert lines[1].startswith(f"gone  {gone}  (cannot load: ")


def test_an_unregistered_stealth_folder_is_pointed_at_stealth_source_add(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(folder)]) == 3
    assert f"holds a stealth source; register it with `loadout stealth add {folder}`" in (
        capsys.readouterr().err
    )


def test_a_registered_stealth_folder_is_pointed_at_sync_stealth_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = make_stealth(tmp_path)
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    capsys.readouterr()
    assert loadout.main(["integrate", "git-hooks", "install", "--root", str(folder)]) == 3
    err = capsys.readouterr().err
    assert "is the registered stealth source app" in err
    assert "`loadout sync --stealth app`" in err


ADDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "from loadout.stealth_registry import RegisteredStealth, update_registry, with_stealth\n"
    "name = sys.argv[1]\n"
    "for i in range(10):\n"
    "    update_registry(lambda cur: with_stealth(cur, RegisteredStealth(f'{name}{i}', Path(f'/{name}/{i}'))))\n"
)


def test_concurrent_updates_each_keep_their_own_entries() -> None:
    children = [subprocess.Popen([sys.executable, "-c", ADDER, name]) for name in ("a", "b")]
    assert [child.wait(timeout=60) for child in children] == [0, 0]
    names = [o.name for o in load_registry(stealth_path())]
    assert names == sorted([f"{n}{i}" for n in "ab" for i in range(10)])
