"""The notice surface as a user meets it.

`tests/test_notices.py` pins what each reporter says; these pin that it reaches a
terminal at all, which is the gap ADR 0015 named. The load-bearing pair is the
last two: a notice must print without moving the exit code, and it must not
silence or be mistaken for real drift.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import loadout
from loadout.emit import collect_notices
from loadout.notices import OPENCODE_SKILL_FLAGS

pytestmark = pytest.mark.integration


def _root_with_hooks(tmp_path: Path, document: dict[str, object]) -> Path:
    """A minimal agent-keyed source with one hooks fragment.

    Built here rather than by mutating the shared fixture: declaring `[claude]`
    turns on every automatic slice, and the shared manifest uses the legacy
    `[permissions.*]` form, so the two together would render outputs this test
    has no reason to care about.
    """
    root = tmp_path / "src"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "main.json").write_text(json.dumps(document), encoding="utf-8")
    (root / "permissions.toml").write_text("[shell]\nallow = ['ls']\n", encoding="utf-8")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[claude]\nhooks = ["main"]\n',
        encoding="utf-8",
    )
    return root


def test_a_notice_reaches_the_terminal(tmp_path: Path, capsys) -> None:
    root = _root_with_hooks(tmp_path, {"NoSuchEvent": []})

    loadout.main(["sync", "--root", str(root), "--force"])
    capsys.readouterr()
    loadout.main(["check", "--root", str(root)])

    assert "note: claude.hooks: NoSuchEvent" in capsys.readouterr().out


def test_a_notice_does_not_move_the_exit_code(tmp_path: Path, capsys) -> None:
    """The distinction the surface rests on. The render is correct — the source
    asked for an event this harness does not know — so `check` must still say the
    generated files are up to date and exit 0."""
    root = _root_with_hooks(tmp_path, {"NoSuchEvent": []})
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "note: claude.hooks: NoSuchEvent" in out
    assert "generated files are up to date" in out


def test_real_drift_still_fails_alongside_a_notice(tmp_path: Path, fake_home: Path, capsys) -> None:
    """A notice must not mask drift, and drift must not suppress the notice — the
    two carry different information and a user needs both."""
    root = _root_with_hooks(tmp_path, {"NoSuchEvent": []})
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    generated = fake_home / ".claude" / "settings.json"
    generated.write_text('{"clobbered": true}\n', encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1
    captured = capsys.readouterr()
    assert "note: claude.hooks: NoSuchEvent" in captured.out
    assert "DRIFT" in captured.err


def test_a_clean_source_says_nothing(tmp_path: Path, capsys) -> None:
    """Silence is the point. A surface that speaks on every run is one nobody
    reads, and a source with no hooks slice and no stated catch-all has nothing
    to report.

    Built here rather than from the shared fixture, which states a `[shell]
    default` on purpose — see `test_a_stated_catch_all_names_the_harnesses_it_
    misses` below for the notice that provokes.
    """
    root = tmp_path / "quiet"
    root.mkdir()
    (root / "permissions.toml").write_text("[shell]\nallow = ['ls']\n", encoding="utf-8")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[claude]\n', encoding="utf-8"
    )
    assert collect_notices(root) == ()

    loadout.main(["sync", "--root", str(root), "--force"])
    capsys.readouterr()
    loadout.main(["check", "--root", str(root)])

    assert "note:" not in capsys.readouterr().out


def test_a_stated_catch_all_names_the_harnesses_it_misses(root: Path, capsys) -> None:
    """The shared fixture states `default = "allow"` and renders Claude, Codex and
    Droid, none of which authors a catch-all. The docs record that; this is the run
    saying it, which is the whole point of ADR 0015's surface.

    The glob assertion pins the other half of the same wiring: `unsupported_shell_globs`
    reaching output through `_permission_notices`, which nothing else exercises.
    """
    loadout.main(["sync", "--root", str(root), "--force"])
    capsys.readouterr()
    loadout.main(["check", "--root", str(root)])
    out = capsys.readouterr().out

    assert "note: claude.permissions: [shell] default" in out
    assert "note: codex.permissions: [shell] default" in out
    assert "note: droid.permissions: [shell] default" in out
    # The carriers render it, so they must stay silent — a notice that fires for
    # everyone teaches nothing.
    assert "opencode.permissions: [shell] default" not in out
    assert "pi.permissions: [shell] default" not in out
    assert "note: droid.permissions: allow glob 'gamma-*'" in out


def test_a_project_only_repo_reports_nothing_rather_than_failing(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project-only repo has no manifest to load, so every global reporter has
    to be skipped rather than asked — asking would raise where reporting nothing
    is correct.

    The fixture's whole harness set is kept. Narrowing it to harnesses that report
    nothing would leave the absent manifest tested against reporters that never
    ran, which passes just as well against a build where the input was never
    constructed. What proves the global reporters were skipped is that the notices
    that *do* come back are all project-scope ones.
    """
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1")
    monkeypatch.setenv("OPENCODE_DISABLE_EXTERNAL_SKILLS", "1")
    messages = [f"{n.agent}.{n.slice}: {n.message}" for n in collect_notices(project)]

    # OpenCode authors its own catch-all at project scope, so it is the one
    # harness that stays silent — the loser that proves the rest were asked.
    assert not any(m.startswith("opencode.") for m in messages)
    assert sum("[shell] default" in m for m in messages) == 4
    assert "droid.permissions: allow glob 'gamma-*' is not rendered" in " | ".join(messages)


def test_check_reports_the_opencode_skills_race_without_failing(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Advisory, like every notice: the source rendered fine and the setup around
    it did not. Asserting exit 0 alongside the message is the load-bearing half —
    a report that moved the exit code would break every pre-commit hook."""
    monkeypatch.delenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_CLAUDE_CODE", raising=False)
    assert loadout.main(["sync", "--root", str(project)]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(project)]) == 0

    assert "opencode.skills" in capsys.readouterr().out


def test_check_reports_the_codex_opencode_skill_race(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "1")
    monkeypatch.delenv("OPENCODE_DISABLE_EXTERNAL_SKILLS", raising=False)

    messages = [notice.message for notice in collect_notices(project)]

    assert any(
        "Codex's" in message and "restrict the skill's agents" in message for message in messages
    )


def test_a_project_without_opencode_is_not_told_about_its_flag(
    project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_CLAUDE_CODE", raising=False)
    config = project / "loadout" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            '"claude", "codex", "droid", "opencode", "pi"', '"claude"'
        ),
        encoding="utf-8",
    )
    loadout.main(["sync", "--root", str(project)])
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(project)]) == 0

    assert "opencode.skills" not in capsys.readouterr().out


def _root_with_skills(tmp_path: Path) -> Path:
    """A global source naming opencode and carrying one skill."""
    root = tmp_path / "src"
    (root / "skills" / "doc").mkdir(parents=True)
    (root / "skills" / "doc" / "SKILL.md").write_text(
        "---\nname: doc\ndescription: d\n---\n\n# Doc\n", encoding="utf-8"
    )
    (root / "permissions.toml").write_text("[shell]\nallow = ['ls']\n", encoding="utf-8")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[claude]\n[opencode]\n',
        encoding="utf-8",
    )
    return root


def test_the_global_skill_race_is_reported_when_no_flag_is_set(tmp_path: Path, monkeypatch) -> None:
    """~/.claude/skills and ~/.config/opencode/skills are both written the moment
    any skill exists and a manifest names OpenCode — earlier than project scope,
    which needs the repo to declare opencode *and* carry skills."""
    for flag in OPENCODE_SKILL_FLAGS:
        monkeypatch.delenv(flag, raising=False)

    found = collect_notices(_root_with_skills(tmp_path))

    assert [n.slice for n in found] == ["skills"]
    assert "picks between the two copies" in found[0].message


def test_either_flag_silences_the_global_race(tmp_path: Path, monkeypatch) -> None:
    """`disableClaudeCodeSkills` is `broad || direct`, so a check reading one name
    reports a collision at whoever set the other."""
    root = _root_with_skills(tmp_path)
    for flag in OPENCODE_SKILL_FLAGS:
        for other in OPENCODE_SKILL_FLAGS:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(flag, "1")
        assert collect_notices(root) == (), flag


def test_a_source_with_no_skills_has_no_collision_to_report(tmp_path: Path, monkeypatch) -> None:
    """The notice is about two copies of one name. With no skills there is no
    name, so silence here is correct rather than a missed report."""
    for flag in OPENCODE_SKILL_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    root = _root_with_skills(tmp_path)
    shutil.rmtree(root / "skills")

    assert collect_notices(root) == ()


def _root_with_unpermitted_server(tmp_path: Path) -> Path:
    """A minimal source with one mcp.toml server and no `[mcp]` policy naming it."""
    root = tmp_path / "src"
    root.mkdir(parents=True)
    (root / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://x"\n', encoding="utf-8"
    )
    (root / "permissions.toml").write_text("[shell]\nallow = ['ls']\n", encoding="utf-8")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[claude]\n', encoding="utf-8"
    )
    return root


def test_an_unpermitted_server_does_not_move_the_exit_code(tmp_path: Path, capsys) -> None:
    """Same contract as every other notice: the render is correct — mcp.toml
    defines a server the `[mcp]` policy never mentions — so `check` must still
    say the generated files are up to date and exit 0."""
    root = _root_with_unpermitted_server(tmp_path)
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "note: mcp.servers: jina is defined" in out
    assert "generated files are up to date" in out


def test_a_server_named_by_policy_says_nothing(tmp_path: Path, capsys) -> None:
    """Any policy entry naming the server is enough — even one tool, not just a
    wildcard — so a source that mentions it must stay silent."""
    root = tmp_path / "src"
    root.mkdir(parents=True)
    (root / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://x"\n', encoding="utf-8"
    )
    (root / "permissions.toml").write_text(
        "[shell]\nallow = ['ls']\n\n[mcp]\nallow = ['jina/search']\n", encoding="utf-8"
    )
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[claude]\n', encoding="utf-8"
    )
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 0
    assert "mcp.servers" not in capsys.readouterr().out


def test_a_native_project_declaring_droid_reports_rather_than_failing(tmp_path: Path) -> None:
    """`presets = false` renders from artifacts and never reads permissions.toml,
    so a reporter that parses it unconditionally failed `check` on a project with
    nothing wrong with it. The notice path has to follow the render path.
    """
    (tmp_path / "loadout" / "settings").mkdir(parents=True)
    (tmp_path / "loadout" / "settings" / "native.json").write_text('{"model": "x"}\n')
    (tmp_path / "loadout" / "config.toml").write_text(
        'harnesses = ["droid"]\nartifacts = "artifacts.toml"\npresets = false\n'
    )
    (tmp_path / "loadout" / "artifacts.toml").write_text(
        '[[artifact]]\nagents = ["droid"]\noutput = ".factory/settings.json"\n'
        'format = "json"\n[artifact.parts]\nsettings = {source = "settings/native.json"}\n'
    )

    assert not (tmp_path / "loadout" / "permissions.toml").exists()
    assert collect_notices(tmp_path) == ()
