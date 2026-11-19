from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout import commands, stealth_lock
from loadout.exclude import read_personal_block
from loadout.written import read_personal, read_written, roots_naming_stealth

pytestmark = pytest.mark.integration

LOCAL = "CLAUDE.local.md"
RULE = ".cursor/rules/mine.mdc"
SKILL = ".claude/skills/probe/SKILL.md"
SETTINGS = ".claude/settings.local.json"
PERSONAL_BODY = (
    '[claude]\ninstructions = ["personal"]\nskills = true\n\n'
    '[cursor]\ninstructions = ["personal"]\n'
)
PERSONAL_FILES = {
    "instructions/personal.md": "Personal rules.\n",
    "skills/probe/SKILL.md": "---\nname: probe\ndescription: Mine.\n---\nMine.\n",
}


@pytest.fixture(autouse=True)
def isolated_git(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "test-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / ".config"))


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _write(folder: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_text(text, encoding="utf-8")


def _sync(root: Path) -> int:
    return loadout.main(["sync", "--root", str(root)])


def _commit(root: Path) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "sync")


TEAM = 'harnesses = ["claude", "cursor"]\ninstructions = ["team"]\n'


def _clone(home: Path, config: str = TEAM, files: dict[str, str] | None = None) -> Path:
    root = home / "app"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(
        root,
        {"loadout/config.toml": config, "loadout/instructions/team.md": "Team.\n", **(files or {})},
    )
    _commit(root)
    assert _sync(root) == 0
    _commit(root)
    return root.resolve()


def _manifest(target: Path, body: str, stealth: str = "") -> str:
    return (
        f'[stealth]\ntargets = ["{target}"]\n{stealth}\n'
        f'[[source]]\nname = "mine"\npath = "."\n\n{body}'
    )


def _source(
    home: Path,
    target: Path,
    body: str = PERSONAL_BODY,
    files: dict[str, str] | None = None,
    stealth: str = "",
) -> Path:
    folder = home / "stealth" / "mine"
    folder.mkdir(parents=True)
    _write(
        folder,
        {
            "loadout.toml": _manifest(target, body, stealth),
            **(PERSONAL_FILES if files is None else files),
        },
    )
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    return folder.resolve()


def _block(root: Path) -> tuple[str, ...]:
    return read_personal_block(root / ".git", root)


def _kit_source(home: Path) -> None:
    source = home / "ac"
    _write(
        source,
        {
            "loadout.toml": '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\n',
            "loadout/kits/mobile-app.toml": 'instructions = ["mobile"]\nskills = ["review"]\n',
            "loadout/kits/instructions/mobile.md": "Kit mobile rules.\n",
            "loadout/kits/skills/review/SKILL.md": (
                "---\nname: review\ndescription: Kit review.\n---\nKit review body.\n"
            ),
        },
    )
    _write(home / ".config", {"loadout/config.toml": f'source = "{source}"\n'})


def test_the_personal_tier_is_written_hidden_and_recorded(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    ignore = (root / ".gitignore").read_bytes()
    _source(fake_home, root)
    capsys.readouterr()

    assert _sync(root) == 0

    assert "Personal rules." in (root / LOCAL).read_text(encoding="utf-8")
    assert "Personal rules." in (root / RULE).read_text(encoding="utf-8")
    assert (root / SKILL).is_file()
    assert _git(root, "status", "--porcelain") == ""
    assert (root / ".gitignore").read_bytes() == ignore
    assert sorted(_block(root)) == [
        "/.claude/skills/probe/",
        "/.cursor/rules/mine.mdc",
        "/.cursor/skills/probe/",
        "/CLAUDE.local.md",
    ]
    record = read_personal(root)
    assert record.contributors == ("mine",)
    assert record.personal[root / LOCAL] == "mine"
    assert record.pending == frozenset()


def test_removing_the_kit_from_the_source_retires_its_files_and_empties_the_block(
    fake_home: Path,
) -> None:
    _kit_source(fake_home)
    root = _clone(fake_home)
    ignore = (root / ".gitignore").read_bytes()
    body = "[claude]\nskills = true\n"
    folder = _source(fake_home, root, body, {}, 'kits = ["mobile-app"]\n')
    assert _sync(root) == 0
    assert "Kit mobile rules." in (root / LOCAL).read_text(encoding="utf-8")
    assert (root / ".claude/skills/review/SKILL.md").is_file()
    assert sorted(_block(root)) == ["/.claude/skills/review/", "/CLAUDE.local.md"]

    _write(folder, {"loadout.toml": _manifest(root, body)})
    assert _sync(root) == 0

    assert not (root / LOCAL).exists()
    assert not (root / ".claude/skills/review").exists()
    assert _block(root) == ()
    assert (root / ".gitignore").read_bytes() == ignore
    assert _git(root, "status", "--porcelain") == ""


def test_a_kit_both_tiers_link_clashes_until_the_team_drops_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _kit_source(fake_home)
    linked = 'harnesses = ["claude"]\ninstructions = ["team"]\nkits = ["mobile-app"]\n'
    root = _clone(fake_home, linked)
    _source(fake_home, root, "[claude]\nskills = true\n", {}, 'kits = ["mobile-app"]\n')
    capsys.readouterr()

    assert _sync(root) == 3
    err = capsys.readouterr().err
    assert "refused: stealth mine: its skill review clashes with the team's skill review" in err
    assert "/.claude/skills/review/" in (root / ".gitignore").read_text().splitlines()
    assert "/.claude/skills/review/" not in _block(root)

    _write(root, {"loadout/config.toml": 'harnesses = ["claude"]\ninstructions = ["team"]\n'})
    _commit(root)
    assert _sync(root) == 0

    lines = (root / ".gitignore").read_text().splitlines()
    assert "/.claude/skills/review/" not in lines
    assert "/CLAUDE.md" in lines
    assert "Kit review body." in (root / ".claude/skills/review/SKILL.md").read_text()
    assert "/.claude/skills/review/" in _block(root)
    assert _git(root, "status", "--porcelain") == " M .gitignore\n"


def test_a_tracked_claude_local_md_is_refused_and_left_as_the_team_wrote_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _write(root, {LOCAL: "The team's.\n"})
    _commit(root)
    _source(fake_home, root)
    capsys.readouterr()

    assert _sync(root) == 3

    assert (root / LOCAL).read_text(encoding="utf-8") == "The team's.\n"
    err = capsys.readouterr().err
    assert "refused: the team tracks CLAUDE.local.md; the stealth source does not write it" in err
    assert "Personal rules." in (root / RULE).read_text(encoding="utf-8")
    assert "/.cursor/rules/mine.mdc" in _block(root)
    assert "/CLAUDE.local.md" not in _block(root)


def test_a_recorded_personal_file_the_team_starts_tracking_is_left_and_forgotten(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    assert _sync(root) == 0
    written = (root / LOCAL).read_text(encoding="utf-8")
    _git(root, "add", "-f", LOCAL)
    _git(root, "commit", "-q", "-m", "track")
    capsys.readouterr()

    assert _sync(root) == 0

    assert (root / LOCAL).read_text(encoding="utf-8") == written
    err = capsys.readouterr().err
    assert (
        "note: the team tracks CLAUDE.local.md; it is left in place and no longer recorded" in err
    )
    record = read_personal(root)
    assert root / LOCAL not in record.personal
    assert root / LOCAL not in read_written(root)
    assert root / RULE in record.personal


def test_a_broken_source_holds_its_files_while_the_team_syncs(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    body = PERSONAL_BODY.replace("skills = true\n", 'settings = "local"\n')
    folder = _source(
        fake_home, root, body, {**PERSONAL_FILES, "settings/local.json": '{"model": "opus"}\n'}
    )
    assert _sync(root) == 0
    block = _block(root)
    assert "/.claude/settings.local.json" in block
    (root / ".claude/settings.json").unlink()
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    capsys.readouterr()

    assert _sync(root) == 3

    assert (root / ".claude/settings.json").is_file()
    assert "Personal rules." in (root / LOCAL).read_text(encoding="utf-8")
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"model": "opus"}
    assert sorted(_block(root)) == sorted(block)
    assert "refused: stealth mine:" in capsys.readouterr().err
    record = read_written(root)
    assert root / LOCAL in record
    assert record[root / SETTINGS].owned == ("model",)


def test_a_hand_written_ignore_line_for_a_personal_file_survives_its_retirement(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    with (root / ".gitignore").open("a", encoding="utf-8") as ignore:
        ignore.write("/CLAUDE.local.md\n")
    _commit(root)
    ignore = (root / ".gitignore").read_bytes()
    folder = _source(fake_home, root)
    assert _sync(root) == 0
    assert (root / LOCAL).is_file()

    _write(folder, {"loadout.toml": _manifest(root, "[claude]\nskills = true\n")})
    assert _sync(root) == 0

    assert not (root / LOCAL).exists()
    assert (root / ".gitignore").read_bytes() == ignore


def test_a_hook_run_with_a_clash_exits_with_the_teams_code(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    team_skill = "---\nname: probe\ndescription: Team.\n---\nTeam.\n"
    root = _clone(fake_home, files={"loadout/skills/probe/SKILL.md": team_skill})
    _source(fake_home, root)
    assert _sync(root) == 3
    capsys.readouterr()

    assert loadout.main(["hook-event", "post-merge", "--root", str(root)]) == 0

    err = capsys.readouterr().err
    assert "refused: stealth mine: its skill probe clashes with the team's skill probe" in err
    skill = (root / SKILL).read_text(encoding="utf-8")
    assert "Team." in skill
    assert "Mine." not in skill


def test_check_reports_drift_in_a_personal_file(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root)
    assert _sync(root) == 0
    _write(folder, {"instructions/personal.md": "Edited.\n"})
    capsys.readouterr()

    assert loadout.main(["check", "--json", "--root", str(root)]) == 1

    drift = json.loads(capsys.readouterr().out)["drift"]
    assert {"path": str(root / LOCAL), "kind": "source-changed"} in drift


def test_a_settings_file_loadout_created_is_deleted_once_empty_and_a_users_is_kept(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    body = PERSONAL_BODY.replace("skills = true\n", 'settings = "local"\n')
    files = {**PERSONAL_FILES, "settings/local.json": '{"model": "opus"}\n'}
    folder = _source(fake_home, root, body, files)
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"model": "opus"}

    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    assert _sync(root) == 0
    assert not (root / SETTINGS).exists()

    (root / SETTINGS).write_text('{"theme": "dark"}\n', encoding="utf-8")
    _write(folder, {"loadout.toml": _manifest(root, body)})
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {
        "theme": "dark",
        "model": "opus",
    }
    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"theme": "dark"}


SETTINGS_BODY = PERSONAL_BODY.replace("skills = true\n", 'settings = "local"\n')
SETTINGS_FILES = {**PERSONAL_FILES, "settings/local.json": '{"model": "opus"}\n'}


def _crash(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(commands, name, boom)


def _interrupted_sync(root: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    with monkeypatch.context() as patch:
        _crash(patch, name)
        with contextlib.suppress(BaseException):
            _sync(root)


def test_a_held_sources_files_that_were_only_pending_survive_the_next_syncs(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root)
    _interrupted_sync(root, monkeypatch, "_record_personal")
    assert root / LOCAL in read_personal(root).pending
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    assert _sync(root) == 3

    assert _sync(root) == 3

    assert (root / LOCAL).is_file()
    assert (root / RULE).is_file()
    assert (root / SKILL).is_file()
    assert "/CLAUDE.local.md" in _block(root)
    assert root / LOCAL in read_personal(root).personal


def test_a_lone_surrogate_in_a_personal_document_survives_loadout_writing_its_keys(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    _write(root, {SETTINGS: '{"note": "\\ud83d"}\n'})
    _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)

    assert _sync(root) == 0

    written = (root / SETTINGS).read_bytes()
    assert b'"\\ud83d"' in written
    assert json.loads(written) == {"note": "\ud83d", "model": "opus"}
    assert _sync(root) == 0
    assert (root / SETTINGS).read_bytes() == written


def test_a_directory_that_cannot_be_flocked_does_not_stop_the_sync(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    real = fcntl.flock

    def flock(fd: int, operation: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.ENOLCK, "no locks available")
        real(fd, operation)

    monkeypatch.setattr(stealth_lock.fcntl, "flock", flock)

    assert _sync(root) == 0

    assert "Personal rules." in (root / LOCAL).read_text(encoding="utf-8")


def test_a_sync_waits_out_then_refuses_while_another_holds_the_project(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    monkeypatch.setattr(stealth_lock, "LOCK_TIMEOUT_SECONDS", 0.2)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    capsys.readouterr()
    try:
        code = _sync(root)
    finally:
        os.close(fd)

    assert code != 0
    assert "another loadout process" in capsys.readouterr().err


def test_a_created_settings_file_is_recorded_before_retirement_can_be_interrupted(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    _interrupted_sync(root, monkeypatch, "retire_orphans")
    assert root / SETTINGS in read_personal(root).created

    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    assert _sync(root) == 0

    assert not (root / SETTINGS).exists()
    assert "/.claude/settings.local.json" not in _block(root)


def test_the_personal_paths_are_recorded_and_hidden_before_any_file_is_written(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    _interrupted_sync(root, monkeypatch, "deploy")

    assert not (root / LOCAL).exists()
    assert root / LOCAL in read_personal(root).pending
    assert "/CLAUDE.local.md" in _block(root)


def test_a_personal_document_changed_while_syncing_is_refused_and_kept(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _write(root, {SETTINGS: '{"theme": "dark"}\n'})
    _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    real = commands.write_documents

    def racing(*args: object, **kwargs: object) -> object:
        (root / SETTINGS).write_text('{"theme": "light"}\n', encoding="utf-8")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(commands, "write_documents", racing)
    capsys.readouterr()

    assert _sync(root) == 3

    assert f"refused: {SETTINGS} changed while syncing" in capsys.readouterr().err
    assert (root / SETTINGS).read_text(encoding="utf-8") == '{"theme": "light"}\n'


def test_a_user_file_loadout_stopped_owning_is_not_deleted_when_the_user_empties_it(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    assert _sync(root) == 0
    document = json.loads((root / SETTINGS).read_text(encoding="utf-8"))
    (root / SETTINGS).write_text(json.dumps({**document, "theme": "dark"}), encoding="utf-8")
    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"theme": "dark"}

    (root / SETTINGS).write_text("{}\n", encoding="utf-8")
    assert _sync(root) == 0

    assert (root / SETTINGS).read_text(encoding="utf-8") == "{}\n"


def test_check_does_not_tell_the_user_to_untrack_a_personal_file_the_team_tracks(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    assert _sync(root) == 0
    _git(root, "add", "-f", LOCAL)
    _git(root, "commit", "-q", "-m", "track")
    capsys.readouterr()

    loadout.main(["check", "--json", "--root", str(root)])

    notes = json.loads(capsys.readouterr().out)["notes"]
    assert [note for note in notes if "Git still tracks" in note] == []


def _other(home: Path, target: Path, body: str, files: dict[str, str]) -> Path:
    folder = home / "stealth" / "other"
    folder.mkdir(parents=True)
    manifest = _manifest(target, body).replace('name = "mine"', 'name = "other"')
    _write(folder, {"loadout.toml": manifest, **files})
    assert loadout.main(["stealth", "add", str(folder)]) == 0
    return folder.resolve()


def test_sync_reports_a_notice_about_a_personal_permission_rule(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home, 'harnesses = ["claude", "codex"]\ninstructions = ["team"]\n')
    _source(fake_home, root, "[codex]\n", {"permissions.toml": '[shell]\ndeny = ["=nu wipe"]\n'})
    capsys.readouterr()

    assert _sync(root) == 0

    assert (
        "note: codex.permissions: deny '=nu wipe' is rendered as 'nu wipe'"
        in capsys.readouterr().out
    )


def test_the_team_syncs_alone_when_the_pending_record_cannot_be_saved(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    (root / "CLAUDE.md").unlink()
    real = commands.record_written

    def failing(*args: object, **kwargs: object) -> object:
        if kwargs.get("pending"):
            return None
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(commands, "record_written", failing)
    capsys.readouterr()

    assert _sync(root) == 3

    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert not (root / LOCAL).exists()
    assert (
        "refused: the written record cannot be saved; the personal tier was not synced"
        in capsys.readouterr().err
    )
    assert _block(root) == ()


def test_a_tracked_team_document_the_personal_tier_would_change_keeps_the_team_bytes(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(
        fake_home, files={"loadout/permissions.toml": '[shell]\nallow = ["git status"]\n'}
    )
    cli = ".cursor/cli.json"
    _git(root, "add", "-f", cli)
    _git(root, "commit", "-q", "-m", "track")
    team = (root / cli).read_bytes()
    _source(
        fake_home, root, files={**PERSONAL_FILES, "permissions.toml": '[shell]\nallow = ["ls"]\n'}
    )
    capsys.readouterr()

    assert _sync(root) == 3

    assert (root / cli).read_bytes() == team
    assert f"refused: the team tracks {cli}" in capsys.readouterr().err
    assert "Personal rules." in (root / LOCAL).read_text(encoding="utf-8")


def test_a_held_sources_keys_stay_owned_in_a_document_another_source_still_renders(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    _other(
        fake_home,
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"theme": "dark"}\n'},
    )
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {
        "model": "opus",
        "theme": "dark",
    }
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")

    assert _sync(root) == 3

    assert read_written(root)[root / SETTINGS].owned == ("model", "theme")
    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"theme": "dark"}


def test_every_held_source_is_still_named_by_the_record(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    mine = _source(fake_home, root)
    other = _other(
        fake_home,
        root,
        '[cursor]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )
    _interrupted_sync(root, monkeypatch, "_record_personal")
    assert root / ".cursor/rules/other.mdc" in read_personal(root).pending
    for folder in (mine, other):
        (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")

    assert _sync(root) == 3

    assert root in roots_naming_stealth("mine")
    assert root in roots_naming_stealth("other")
