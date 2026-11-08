from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from loadout.stealth_registry import marked_removing, update_registry
from test_stealth_cli import _git, _stealth
from test_stealth_kits import kit_source

pytestmark = pytest.mark.integration

NAME = "fixture"
KIT_SKILL = ".claude/skills/review/SKILL.md"


@pytest.fixture(autouse=True)
def isolated_git(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "test-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _setup(home: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    kit_source(home, monkeypatch)
    folder, (target,) = _stealth(home, "a")
    return folder, target


def test_kit_add_links_a_kit_and_sync_renders_it(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, target = _setup(fake_home, monkeypatch)
    manifest = folder / "loadout.toml"
    before = manifest.read_text(encoding="utf-8")

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0

    after = manifest.read_text(encoding="utf-8")
    assert after == before.replace(
        'targets = ["~/stealth-fixture*"]\n',
        'targets = ["~/stealth-fixture*"]\nkits = ["mobile-app"]\n',
    )
    out = capsys.readouterr().out
    assert "linked mobile-app in ~/stealth/fixture/loadout.toml" in out
    assert "resolves from source ac:" in out
    assert loadout.main(["sync", "--stealth", NAME]) == 0
    assert "Kit mobile rules." in (target / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert "Kit review body." in (target / KIT_SKILL).read_text(encoding="utf-8")
    assert loadout.main(["check", "--stealth", NAME]) == 0
    assert _git(target, "status", "--porcelain") == ""


def test_kit_list_shows_where_each_linked_kit_resolves(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(fake_home, monkeypatch)
    capsys.readouterr()
    assert loadout.main(["kit", "list", "--stealth", NAME]) == 0
    assert capsys.readouterr().out == (
        f"no kits in stealth source {NAME} — add one with "
        f"`loadout kit add <name> --stealth {NAME}`\n"
    )
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0
    capsys.readouterr()

    assert loadout.main(["kit", "list", "--stealth", NAME]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "mobile-app: linked, from source ac"
    assert lines[1].endswith("kits/mobile-app.toml")


def test_adding_a_linked_kit_again_changes_nothing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0
    linked = (folder / "loadout.toml").read_bytes()
    capsys.readouterr()

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0

    assert capsys.readouterr().out.startswith("mobile-app is already linked in ")
    assert (folder / "loadout.toml").read_bytes() == linked


@pytest.mark.parametrize(
    "case",
    [
        (["mobile-app", "--vendored"], 2, "a stealth source links its kits"),
        (["tablet"], 3, "kit 'tablet' not found"),
        ([], 2, "`kit add` needs a name when not attached to a terminal"),
    ],
)
def test_a_refused_kit_add_leaves_the_manifest_byte_identical(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[list[str], int, str],
) -> None:
    argv, code, message = case
    folder, _ = _setup(fake_home, monkeypatch)
    before = (folder / "loadout.toml").read_bytes()
    capsys.readouterr()

    assert loadout.main(["kit", "add", *argv, "--stealth", NAME]) == code

    assert message in capsys.readouterr().err
    assert (folder / "loadout.toml").read_bytes() == before


def test_kit_add_names_the_registered_stealth_sources_for_an_unknown_name(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(fake_home, monkeypatch)
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", "other"]) == 3
    assert "registered stealth sources: fixture" in capsys.readouterr().err


def test_kit_add_with_both_root_and_stealth_is_a_usage_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    argv = ["kit", "add", "mobile-app", "--root", str(folder), "--stealth", NAME]
    assert loadout.main(argv) == 2
    assert "not allowed with argument" in capsys.readouterr().err


def test_unlinking_a_kit_retires_what_it_rendered(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, target = _setup(fake_home, monkeypatch)
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0
    assert loadout.main(["sync", "--stealth", NAME]) == 0
    assert (target / KIT_SKILL).is_file()
    assert "Kit mobile rules." in (target / "CLAUDE.local.md").read_text(encoding="utf-8")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('kits = ["mobile-app"]\n', ""),
        encoding="utf-8",
    )

    assert loadout.main(["sync", "--stealth", NAME]) == 0

    assert not (target / KIT_SKILL).exists()
    assert "Kit mobile rules." not in (target / "CLAUDE.local.md").read_text(encoding="utf-8")
    assert "personal" in (target / "CLAUDE.local.md").read_text(encoding="utf-8").lower()
    assert loadout.main(["check", "--stealth", NAME]) == 0


def test_a_change_in_a_linked_kit_shows_as_drift_until_sync(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = _setup(fake_home, monkeypatch)
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0
    assert loadout.main(["sync", "--stealth", NAME]) == 0
    part = fake_home / "ac" / "loadout" / "kits" / "instructions" / "mobile.md"
    part.write_text("Kit mobile rules, revised.\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--stealth", NAME]) == 1
    assert "DRIFT (source-changed): ~/stealth-fixture-a/CLAUDE.local.md" in capsys.readouterr().err
    assert loadout.main(["sync", "--stealth", NAME]) == 0
    assert "revised" in (target / "CLAUDE.local.md").read_text(encoding="utf-8")


def test_a_kit_already_linked_under_another_spelling_changes_nothing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    assert loadout.main(["kit", "add", "ac/mobile-app", "--stealth", NAME]) == 0
    linked = (folder / "loadout.toml").read_bytes()
    capsys.readouterr()

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0

    assert "is already linked in" in capsys.readouterr().out
    assert (folder / "loadout.toml").read_bytes() == linked


def test_kit_list_names_a_kit_that_cannot_be_linked_and_keeps_listing(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    hooked = fake_home / "ac" / "loadout" / "kits" / "hooked"
    hooked.mkdir()
    (hooked / "instructions.md").write_text("hooked\n", encoding="utf-8")
    (hooked / "hooks.json").write_text("{}\n", encoding="utf-8")
    manifest = folder / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'targets = ["~/stealth-fixture*"]\n',
            'targets = ["~/stealth-fixture*"]\nkits = ["hooked", "mobile-app"]\n',
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["kit", "list", "--stealth", NAME]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "hooked: unresolved"
    assert "hooks.json" in lines[1]
    assert lines[2] == "mobile-app: linked, from source ac"


def test_kit_list_refuses_a_top_level_kits_key(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    manifest = folder / "loadout.toml"
    manifest.write_text(
        'kits = ["mobile-app"]\n\n' + manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    capsys.readouterr()

    assert loadout.main(["kit", "list", "--stealth", NAME]) == 3

    assert "kits belongs in the [stealth] table" in capsys.readouterr().err


def test_kit_sync_for_a_stealth_source_is_a_usage_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(fake_home, monkeypatch)
    capsys.readouterr()
    assert loadout.main(["kit", "sync", "mobile-app", "--stealth", NAME]) == 2
    assert "keeps no vendored copy" in capsys.readouterr().err


def test_kit_add_keeps_crlf_line_endings(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    manifest = folder / "loadout.toml"
    crlf = manifest.read_bytes().replace(b"\n", b"\r\n")
    manifest.write_bytes(crlf)

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0

    assert manifest.read_bytes() == crlf.replace(
        b'targets = ["~/stealth-fixture*"]\r\n',
        b'targets = ["~/stealth-fixture*"]\r\nkits = ["mobile-app"]\r\n',
    )


def test_kit_add_names_copies_the_kit_already_supplies(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    kits = fake_home / "ac" / "loadout" / "kits"
    (folder / "skills" / "review").mkdir(parents=True)
    for relative in ("SKILL.md", "references/list.md"):
        copy = folder / "skills" / "review" / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes((kits / "skills" / "review" / relative).read_bytes())
    personal = folder / "instructions" / "personal.md"
    personal.write_bytes((kits / "instructions" / "mobile.md").read_bytes())
    capsys.readouterr()

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 0

    notes = [line for line in capsys.readouterr().out.splitlines() if line.startswith("note: ")]
    assert notes == [
        (
            "note: ~/stealth/fixture/skills/review is byte for byte the skill mobile-app "
            "supplies; while it is there it replaces the kit's and stops following it, so "
            "delete it to take the kit's: rm -r ~/stealth/fixture/skills/review"
        ),
        (
            "note: ~/stealth/fixture/instructions/personal.md is byte for byte an instruction "
            "part mobile-app supplies, so both copies are rendered; remove personal from the "
            "instructions lists and delete the file"
        ),
    ]


def test_kit_commands_refuse_a_stealth_source_being_removed(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder, _ = _setup(fake_home, monkeypatch)
    update_registry(lambda current: marked_removing(current, NAME))
    before = (folder / "loadout.toml").read_bytes()
    capsys.readouterr()

    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME]) == 3
    assert loadout.main(["kit", "list", "--stealth", NAME]) == 3

    assert capsys.readouterr().err.count(f"run `loadout stealth remove {NAME}`") == 2
    assert (folder / "loadout.toml").read_bytes() == before


def test_kit_add_with_yes_for_a_stealth_source_is_a_usage_error(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _setup(fake_home, monkeypatch)
    capsys.readouterr()
    assert loadout.main(["kit", "add", "mobile-app", "--stealth", NAME, "--yes"]) == 2
    assert "--yes has nothing to confirm for a stealth source" in capsys.readouterr().err
