from __future__ import annotations

import contextlib
import errno
import fcntl
import json
import os
import stat
import subprocess
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

import loadout
from loadout import commands, personal_sync, stealth_commands, stealth_lock, stealth_sync
from loadout.exclude import read_personal_block
from loadout.init_workflow import INTERRUPTED
from loadout.output import Merged
from loadout.stealth_registry import load_registry, stealth_path
from loadout.written import read_personal, read_written, roots_naming_stealth

pytestmark = pytest.mark.integration

LOCAL = "CLAUDE.local.md"
RULE = ".cursor/rules/mine.mdc"
SKILL = ".claude/skills/probe/SKILL.md"
SETTINGS = ".claude/settings.local.json"
CODEX = ".codex/config.toml"
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


def test_a_personal_skill_the_team_takes_over_leaves_the_block(fake_home: Path) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    assert _sync(root) == 0
    assert sorted(_block(root)) == [
        "/.claude/skills/probe/",
        "/.cursor/rules/mine.mdc",
        "/.cursor/skills/probe/",
        "/CLAUDE.local.md",
    ]
    _write(root, {"loadout/skills/probe/SKILL.md": "---\nname: probe\ndescription: T.\n---\nT.\n"})
    _commit(root)

    assert _sync(root) == 3

    assert "T." in (root / SKILL).read_text(encoding="utf-8")
    assert sorted(_block(root)) == ["/.cursor/rules/mine.mdc", "/CLAUDE.local.md"]


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
    assert (
        "refused: stealth mine: the team tracks CLAUDE.local.md; the stealth source does not "
        "write it"
    ) in err
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
    out = capsys.readouterr().out
    assert (
        "note: the team tracks CLAUDE.local.md; it is left in place and no longer recorded"
        in out.splitlines()
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
    refusals = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("refused: stealth mine:")
    ]
    assert len(refusals) == 1
    assert str(folder / "loadout.toml") in refusals[0]
    record = read_written(root)
    assert root / LOCAL in record
    assert record[root / SETTINGS].owned == ("model",)


def test_check_counts_a_held_sources_skill_as_loadouts(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root)
    assert _sync(root) == 0
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    capsys.readouterr()

    loadout.main(["check", "--json", "--root", str(root)])

    assert (root / SKILL).is_file()
    assert json.loads(capsys.readouterr().out)["drift"] == []


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

    monkeypatch.setattr(personal_sync, name, boom)


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
    real = personal_sync.write_documents

    def racing(*args: object, **kwargs: object) -> object:
        (root / SETTINGS).write_text('{"theme": "light"}\n', encoding="utf-8")
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(personal_sync, "write_documents", racing)
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


@pytest.mark.parametrize(
    ("other", "refusal"),
    [
        ("cursor", "refused: stealth mine: the team tracks CLAUDE.local.md;"),
        ("claude", "refused: the team tracks CLAUDE.local.md;"),
    ],
    ids=["one-source-writes-it", "both-sources-write-it"],
)
def test_a_tracked_path_refusal_names_the_one_source_that_writes_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str], other: str, refusal: str
) -> None:
    root = _clone(fake_home)
    _write(root, {LOCAL: "The team's.\n"})
    _commit(root)
    _source(fake_home, root)
    _other(
        fake_home,
        root,
        f'[{other}]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )
    capsys.readouterr()

    assert _sync(root) == 3

    err = capsys.readouterr().err
    assert refusal in err
    assert err.count("the team tracks CLAUDE.local.md") == 1
    assert (root / LOCAL).read_text(encoding="utf-8") == "The team's.\n"


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
    real = personal_sync.record_written

    def failing(*args: object, **kwargs: object) -> object:
        if kwargs.get("pending"):
            return None
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(personal_sync, "record_written", failing)
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
    assert f"refused: stealth mine: the team tracks {cli}" in capsys.readouterr().err
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


def test_a_held_sources_deny_stays_in_the_team_document_until_the_source_is_fixed(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(
        fake_home, files={"loadout/permissions.toml": '[shell]\nallow = ["git status"]\n'}
    )
    folder = _source(
        fake_home, root, files={**PERSONAL_FILES, "permissions.toml": '[shell]\ndeny = ["rm"]\n'}
    )
    manifest = (folder / "loadout.toml").read_text(encoding="utf-8")
    settings = root / ".claude/settings.json"
    assert _sync(root) == 0
    assert "Bash(rm:*)" in settings.read_text(encoding="utf-8")
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    _write(root, {"loadout/permissions.toml": '[shell]\nallow = ["git status", "ls"]\n'})
    capsys.readouterr()

    assert _sync(root) == 3

    held = settings.read_text(encoding="utf-8")
    assert "Bash(rm:*)" in held
    assert "Bash(ls:*)" not in held
    assert (
        "refused: stealth mine: .claude/settings.json is left as it is while the source is "
        "held, so none of its rules are dropped"
    ) in capsys.readouterr().err
    (folder / "loadout.toml").write_text(manifest, encoding="utf-8")
    assert _sync(root) == 0
    fixed = settings.read_text(encoding="utf-8")
    assert "Bash(rm:*)" in fixed
    assert "Bash(ls:*)" in fixed


def test_a_held_sources_instructions_stay_in_a_document_another_source_still_renders(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root)
    _other(
        fake_home,
        root,
        '[claude]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )
    assert _sync(root) == 0
    both = (root / LOCAL).read_text(encoding="utf-8")
    assert "Personal rules." in both
    assert "Other rules." in both
    (folder / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    capsys.readouterr()

    assert _sync(root) == 3

    assert (root / LOCAL).read_text(encoding="utf-8") == both
    assert f"refused: stealth mine: {LOCAL} is left as it is" in capsys.readouterr().err
    assert root / LOCAL in read_written(root)
    assert root / LOCAL in read_personal(root).personal
    assert "/CLAUDE.local.md" in _block(root)


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


def _registered() -> dict[str, bool]:
    return {entry.name: entry.removing for entry in load_registry(stealth_path())}


def test_removing_one_of_two_sources_writing_one_file_hands_it_to_the_other_and_finishes(
    fake_home: Path,
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    _other(
        fake_home,
        root,
        '[claude]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )
    assert _sync(root) == 0
    assert read_personal(root).personal[root / LOCAL] == "mine"

    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 0

    local = (root / LOCAL).read_text(encoding="utf-8")
    assert "Other rules." in local
    assert "Personal rules." not in local
    assert read_personal(root).personal[root / LOCAL] == "other"
    assert _registered() == {"other": False}
    assert _sync(root) == 0


def _edited_local(home: Path) -> tuple[Path, str]:
    root = _clone(home)
    _source(home, root)
    assert _sync(root) == 0
    edited = (root / LOCAL).read_text(encoding="utf-8") + "Mine.\n"
    (root / LOCAL).write_text(edited, encoding="utf-8")
    return root, edited


def test_removing_a_source_whose_personal_file_was_edited_stops_and_names_force(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, edited = _edited_local(fake_home)
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 1

    err = capsys.readouterr().err
    assert (
        "To finish removing stealth mine and keep the edited file, run "
        "`loadout stealth remove mine --force`."
    ) in err
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert read_personal(root).personal[root / LOCAL] == "mine"
    assert _registered() == {"mine": True}


def test_force_removing_a_source_keeps_its_edited_personal_file_and_finishes(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, edited = _edited_local(fake_home)
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 0

    captured = capsys.readouterr()
    assert f"WARNING: kept {LOCAL}: it was modified since loadout wrote it" in captured.err
    assert f"retired {RULE}" in captured.out
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert not (root / RULE).exists()
    assert read_personal(root).personal == {}
    assert root / LOCAL not in read_written(root)
    assert _registered() == {}
    assert _sync(root) == 0
    assert (root / LOCAL).read_text(encoding="utf-8") == edited


def _claude_other(home: Path, root: Path) -> Path:
    return _other(
        home,
        root,
        '[claude]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )


def test_force_removing_a_source_hands_an_edited_file_another_source_writes_to_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    _claude_other(fake_home, root)
    assert _sync(root) == 0
    assert read_personal(root).personal[root / LOCAL] == "mine"
    edited = (root / LOCAL).read_text(encoding="utf-8") + "Mine.\n"
    (root / LOCAL).write_text(edited, encoding="utf-8")
    capsys.readouterr()
    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 1
    assert (
        "To finish removing stealth mine and keep the edited file, run "
        "`loadout stealth remove mine --force`."
    ) in capsys.readouterr().err

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 0

    captured = capsys.readouterr()
    assert (
        f"WARNING: left {LOCAL} as it is: it was modified since loadout wrote it, and loadout "
        "still manages it, so the next `loadout sync` stops on it"
    ) in captured.err.splitlines()
    assert f"retired {RULE}" in captured.out
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert read_personal(root).personal[root / LOCAL] == "other"
    assert read_personal(root).contributors == ("other",)
    assert root / LOCAL in read_written(root)
    assert _registered() == {"other": False}
    assert _sync(root) == 1
    assert (root / LOCAL).read_text(encoding="utf-8") == edited


def test_force_removing_a_source_keeps_another_sources_edited_orphan_recorded(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    other = _other(
        fake_home,
        root,
        '[cursor]\ninstructions = ["other"]\n',
        {"instructions/other.md": "Other rules.\n"},
    )
    assert _sync(root) == 0
    rule = root / ".cursor/rules/other.mdc"
    manifest = _manifest(root, '[claude]\ninstructions = ["other"]\n')
    _write(other, {"loadout.toml": manifest.replace('name = "mine"', 'name = "other"')})
    edited = rule.read_text(encoding="utf-8") + "Mine.\n"
    rule.write_text(edited, encoding="utf-8")
    assert _sync(root) == 1
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 0

    assert (
        "WARNING: left .cursor/rules/other.mdc as it is: it was modified since loadout wrote it"
    ) in capsys.readouterr().err
    assert rule.read_text(encoding="utf-8") == edited
    assert read_personal(root).personal[rule] == "other"
    assert rule in read_written(root)
    assert "/.cursor/rules/other.mdc" in _block(root)
    assert not (root / RULE).exists()
    assert _registered() == {"other": False}
    assert _sync(root) == 1
    assert rule.read_text(encoding="utf-8") == edited


def test_force_removing_a_source_leaves_an_edited_team_file_and_says_how_to_resolve_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    assert _sync(root) == 0
    team = root / "AGENTS.md"
    original = team.read_text(encoding="utf-8")
    team.write_text(original + "Mine.\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 1

    err = capsys.readouterr().err
    assert "WARNING: AGENTS.md was modified outside loadout (hand-edited)" in err
    assert (
        "To finish removing stealth mine, port the edit to AGENTS.md into the project source "
        "with /loadout, or undo it as the diff shows, then run `loadout stealth remove mine` "
        "again."
    ) in err.splitlines()
    assert team.read_text(encoding="utf-8") == original + "Mine.\n"
    assert read_personal(root).personal[root / LOCAL] == "mine"
    assert _registered() == {"mine": True}
    team.write_text(original, encoding="utf-8")
    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 0
    assert _registered() == {}


def test_a_removal_stopped_by_a_team_and_a_personal_edit_names_both_ways_out(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, edited = _edited_local(fake_home)
    team = root / "AGENTS.md"
    original = team.read_text(encoding="utf-8")
    team.write_text(original + "Mine.\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 1

    err = capsys.readouterr().err
    assert (
        "To finish removing stealth mine, port the edit to AGENTS.md into the project source "
        "with /loadout, or undo it as the diff shows, then run `loadout stealth remove mine "
        "--force` again."
    ) in err.splitlines()
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert read_personal(root).personal[root / LOCAL] == "mine"
    team.write_text(original, encoding="utf-8")
    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 0
    assert (root / LOCAL).read_text(encoding="utf-8") == edited
    assert _registered() == {}


def test_an_unparsable_document_of_the_source_being_removed_names_the_removal_to_rerun(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    assert _sync(root) == 0
    (root / SETTINGS).write_text("{ broken\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes", "--force"]) == 3

    assert (
        f"refused: stealth mine: {SETTINGS} is not a JSON document loadout can remove its keys "
        "from; fix or delete it, then run `loadout stealth remove mine`"
    ) in capsys.readouterr().err.splitlines()
    assert _registered() == {"mine": True}
    (root / SETTINGS).unlink()
    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 0
    assert _registered() == {}


def test_an_unparsable_document_the_remaining_source_writes_is_refused_in_its_name(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    _other(
        fake_home,
        root,
        '[claude]\nsettings = "local"\n',
        {"settings/local.json": '{"theme": "dark"}\n'},
    )
    assert _sync(root) == 0
    assert read_personal(root).personal[root / SETTINGS] == "mine"
    (root / SETTINGS).write_text("{ broken\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 3

    assert (
        f"refused: stealth other: {SETTINGS} is not a JSON document loadout can write its keys "
        "into; fix or delete it, then run `loadout sync`"
    ) in capsys.readouterr().err.splitlines()
    assert _registered() == {"other": False}
    (root / SETTINGS).unlink()
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"theme": "dark"}


def test_the_files_written_are_reported_when_a_later_step_fails(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    capsys.readouterr()

    with monkeypatch.context() as patch:
        _crash(patch, "retire_orphans")
        assert _sync(root) == INTERRUPTED

    captured = capsys.readouterr()
    assert "loadout: sync stopped with Ctrl-C; run it again to finish" in captured.err.splitlines()
    assert f"wrote {LOCAL}" in captured.out.splitlines()
    assert (root / LOCAL).is_file()


def test_an_unparsable_personal_document_is_refused_alone_and_the_team_still_syncs(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    assert _sync(root) == 0
    (root / SETTINGS).write_text("{ broken\n", encoding="utf-8")
    _write(root, {"loadout/instructions/team.md": "Team edited.\n"})
    _write(folder, {"instructions/personal.md": "Personal edited.\n"})
    capsys.readouterr()

    assert _sync(root) == 3

    err = capsys.readouterr().err
    assert (
        f"refused: stealth mine: {SETTINGS} is not a JSON document loadout can write its keys "
        "into; fix or delete it, then run `loadout sync`"
    ) in err.splitlines()
    assert "Team edited." in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Personal edited." in (root / LOCAL).read_text(encoding="utf-8")
    assert (root / SETTINGS).read_text(encoding="utf-8") == "{ broken\n"
    assert root / SETTINGS in read_personal(root).personal
    assert root / SETTINGS in read_written(root)
    assert "/.claude/settings.local.json" in _block(root)
    capsys.readouterr()
    assert loadout.main(["check", "--json", "--root", str(root)]) == 1
    drift = json.loads(capsys.readouterr().out)["drift"]
    assert {"path": str(root / SETTINGS), "kind": "unparsable"} in drift


def test_check_advises_fixing_an_unparsable_personal_document_and_sync_then_writes_it(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    assert _sync(root) == 0
    (root / SETTINGS).write_text("{ broken\n", encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    assert (
        "  1 unparsable: a file loadout shares with a harness no longer parses; fix or delete it, "
        "then run `loadout sync`; `loadout sync --force` replaces one the source still renders, "
        "but not one a stealth source writes."
    ) in capsys.readouterr().err.splitlines()
    assert loadout.main(["sync", "--force", "--root", str(root)]) == 3
    assert (root / SETTINGS).read_text(encoding="utf-8") == "{ broken\n"
    (root / SETTINGS).unlink()
    assert _sync(root) == 0
    assert json.loads((root / SETTINGS).read_text(encoding="utf-8")) == {"model": "opus"}


def test_an_unparsable_personal_document_no_longer_rendered_is_refused_alone(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    folder = _source(fake_home, root, SETTINGS_BODY, SETTINGS_FILES)
    assert _sync(root) == 0
    (root / SETTINGS).write_text("{ broken\n", encoding="utf-8")
    _write(folder, {"loadout.toml": _manifest(root, PERSONAL_BODY)})
    _write(
        folder, {"skills/probe/SKILL.md": "---\nname: probe\ndescription: Mine.\n---\nEdited.\n"}
    )
    _write(root, {"loadout/instructions/team.md": "Team edited.\n"})
    capsys.readouterr()

    assert _sync(root) == 3

    err = capsys.readouterr().err
    assert (
        f"refused: stealth mine: {SETTINGS} is not a JSON document loadout can remove its keys "
        "from; fix or delete it, then run `loadout sync`"
    ) in err.splitlines()
    assert "Team edited." in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert (root / SKILL).read_text(encoding="utf-8").endswith("Edited.\n")
    assert (root / SETTINGS).read_text(encoding="utf-8") == "{ broken\n"
    assert root / SETTINGS in read_written(root)


def test_an_unrecorded_unparsable_codex_config_is_refused_alone_and_the_rest_syncs(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home, 'harnesses = ["claude", "codex"]\ninstructions = ["team"]\n')
    _write(root, {CODEX: "model = = broken\n", "loadout/instructions/team.md": "Team edited.\n"})
    _source(
        fake_home,
        root,
        '[claude]\ninstructions = ["personal"]\n\n[codex]\ninstructions = ["personal"]\n',
        {"instructions/personal.md": "Personal rules.\n"},
    )
    capsys.readouterr()

    assert _sync(root) == 3

    assert (
        f"refused: stealth mine: {CODEX} is not a TOML document loadout can write its keys into; "
        "fix or delete it, then run `loadout sync`"
    ) in capsys.readouterr().err.splitlines()
    assert (root / CODEX).read_text(encoding="utf-8") == "model = = broken\n"
    assert root / CODEX not in read_personal(root).personal
    assert "Team edited." in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Personal rules." in (root / LOCAL).read_text(encoding="utf-8")


def test_a_document_that_stopped_parsing_before_its_write_is_refused_and_left_alone(
    tmp_path: Path,
) -> None:
    path = tmp_path / SETTINGS
    path.parent.mkdir(parents=True)
    path.write_text("{ broken\n", encoding="utf-8")
    merged = Merged(frozenset({"model"}), '{"model": "opus"}', format="json")

    done = personal_sync.write_documents(tmp_path, {path: merged}, {path: b"{ broken\n"})

    assert done.refused == (path,)
    refusal = (
        f"refused: {SETTINGS} is not a JSON document loadout can write its keys into; fix or "
        "delete it, then run `loadout sync`"
    )
    assert done.refusals == (refusal,)
    assert path.read_text(encoding="utf-8") == "{ broken\n"


def test_stealth_remove_never_holds_the_stealth_and_project_locks_at_once(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _clone(fake_home)
    _source(fake_home, root)
    assert _sync(root) == 0
    events: list[tuple[str, str]] = []
    held: set[str] = set()

    def recording(kind: str, real: Callable[[Path], AbstractContextManager[object]]) -> object:
        @contextlib.contextmanager
        def lock(path: Path) -> Iterator[None]:
            assert not held, f"{kind} lock taken while {held} is held"
            with real(path):
                held.add(kind)
                events.append((kind, "enter"))
                try:
                    yield
                finally:
                    held.discard(kind)
                    events.append((kind, "exit"))

        return lock

    monkeypatch.setattr(commands, "project_lock", recording("project", commands.project_lock))
    for module in (stealth_commands, stealth_sync):
        monkeypatch.setattr(module, "stealth_lock", recording("stealth", module.stealth_lock))

    assert loadout.main(["stealth", "remove", "mine", "--yes"]) == 0

    assert events == [
        ("stealth", "enter"),
        ("stealth", "exit"),
        ("project", "enter"),
        ("project", "exit"),
        ("stealth", "enter"),
        ("stealth", "exit"),
    ]
    assert _registered() == {}


def test_a_personal_file_the_team_re_includes_is_refused_naming_its_source(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _clone(fake_home)
    ignore = root / ".gitignore"
    ignore.write_text(ignore.read_text(encoding="utf-8") + f"!/{LOCAL}\n", encoding="utf-8")
    _commit(root)
    line = len(ignore.read_text(encoding="utf-8").splitlines())
    _source(fake_home, root)
    capsys.readouterr()

    assert _sync(root) == 3

    assert (
        f"refused: stealth mine: {LOCAL} would show in git status: "
        f".gitignore:{line}:!/{LOCAL} re-includes it"
    ) in capsys.readouterr().err.splitlines()
    assert not (root / LOCAL).exists()
    assert (root / RULE).is_file()
