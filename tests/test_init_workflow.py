from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout import migration_transaction
from loadout.discovery import discover
from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.written import written_state_path

pytestmark = pytest.mark.migration_integration


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Fixture")
    _git(root, "config", "user.email", "fixture@example.invalid")


def test_unresolved_json_preview_does_not_mutate(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text("rules\n")
    assert (
        loadout.main(["init", "project", "--root", str(tmp_path), "--dry-run", "--json", "--yes"])
        == 2
    )
    data = json.loads(capsys.readouterr().out)
    assert {i["code"] for i in data["issues"]} == {"agents-required", "source-mapping"}
    assert list(tmp_path.iterdir()) == [tmp_path / "AGENTS.md"]


def test_global_defaults_to_cwd_and_registers_actual_manifest(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "global", "--harness", "pi", "--yes"]) == 0
    data = tomllib.loads(machine_config_path().read_text())
    assert data == {"source": str(tmp_path / "loadout"), "harnesses": ["pi"]}
    assert loadout.main(["check", "--global"]) == 0
    capsys.readouterr()
    assert loadout.main(["init", "global", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["already_initialized"]


def test_project_init_uses_machine_harness_defaults(tmp_path, fake_home, capsys):
    source = fake_home / "global"
    source.mkdir()
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text(f'source = "{source}"\nharnesses = ["claude", "droid"]\n')

    assert loadout.main(["init", "project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agents"] == ["claude", "droid"]


def test_machine_harness_defaults_take_precedence_over_project_discovery(
    tmp_path, fake_home, capsys
):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}\n")
    assert discover(tmp_path, scope="project").agents == ("claude",)

    source = fake_home / "global"
    source.mkdir()
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text(f'source = "{source}"\nharnesses = ["droid"]\n')

    assert loadout.main(["init", "project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agents"] == ["droid"]


def test_stale_global_source_does_not_block_machine_harness_defaults(tmp_path, capsys):
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text('source = "/nope/missing"\nharnesses = ["droid"]\n')

    assert loadout.main(["init", "project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agents"] == ["droid"]


def test_explicit_project_harnesses_override_machine_defaults(tmp_path, fake_home, capsys):
    source = fake_home / "global"
    source.mkdir()
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text(f'source = "{source}"\nharnesses = ["droid"]\n')

    assert (
        loadout.main(
            [
                "init",
                "project",
                "--root",
                str(tmp_path),
                "--harness",
                "claude",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["agents"] == ["claude"]


def test_initialized_project_ignores_machine_harness_defaults(tmp_path, fake_home, capsys):
    _repo(tmp_path)
    assert (
        loadout.main(
            [
                "init",
                "project",
                "--root",
                str(tmp_path),
                "--harness",
                "claude",
                "--yes",
                "--json",
            ]
        )
        == 0
    )
    source = fake_home / "global"
    source.mkdir()
    machine = machine_config_path()
    machine.parent.mkdir(parents=True, exist_ok=True)
    machine.write_text(f'source = "{source}"\nharnesses = ["droid"]\n')
    capsys.readouterr()

    assert loadout.main(["init", "project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["agents"] == ["claude"]
    assert preview["already_initialized"]


def test_repeat_global_init_saves_defaults_and_preserves_machine_config(
    tmp_path, monkeypatch, capsys
):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "global", "--harness", "pi", "--yes"]) == 0
    machine = machine_config_path()
    source = tmp_path / "loadout"
    machine.write_text(
        f'# selected here\nsource = "{source}"\nprofile = "autonomous"\nharnesses = ["claude"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["init", "global", "--yes", "--json"]) == 0
    assert machine.read_text(encoding="utf-8") == (
        f'# selected here\nsource = "{source}"\nprofile = "autonomous"\nharnesses = ["pi"]\n'
    )


def test_matching_registration_keep_preserves_harness_defaults(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "global", "--harness", "pi", "--yes"]) == 0
    machine = machine_config_path()
    source = tmp_path / "loadout"
    machine.write_text(
        f'source = "{source}"\nharnesses = ["claude"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["init", "global", "--registration", "keep", "--yes", "--json"]) == 0
    assert tomllib.loads(machine.read_text(encoding="utf-8"))["harnesses"] == ["claude"]


def test_global_init_registers_the_directory_it_is_given(tmp_path, monkeypatch):
    """Given a directory, cwd plays no part: the loser is a cwd that would register elsewhere."""
    target = tmp_path / "config-repo"
    target.mkdir()
    _repo(target)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert loadout.main(["init", "global", str(target), "--harness", "pi", "--yes"]) == 0

    registered = tomllib.loads(machine_config_path().read_text())["source"]
    assert registered == str(target / "loadout")
    assert list(elsewhere.iterdir()) == []


def test_a_conflicting_registration_names_the_flag_that_replaces_it(tmp_path, capsys):
    """The refusal users follow, so the flag it names has to exist."""
    _repo(tmp_path)
    init = ["init", "global", str(tmp_path), "--harness", "claude", "--yes"]
    assert loadout.main(init) == 0
    machine_config_path().write_text('source = "/different"\n')
    capsys.readouterr()

    assert loadout.main(init) == 2

    assert "--registration replace" in capsys.readouterr().err
    assert loadout.main([*init, "--registration", "replace"]) == 0
    assert tomllib.loads(machine_config_path().read_text())["source"] == str(tmp_path / "loadout")


def test_global_copy_conflict_requires_selection_even_with_yes(tmp_path, fake_home, capsys):
    _repo(tmp_path)
    live = fake_home / ".claude"
    live.mkdir()
    (live / "settings.json").write_text('{"model":"live"}\n')
    source = tmp_path / "claude"
    source.mkdir()
    (source / "settings.json").write_text('{"model":"source"}\n')
    mapping = json.dumps({"source": str(source), "destination": str(live), "agents": ["claude"]})
    args = [
        "init",
        "global",
        str(tmp_path),
        "--harness",
        "claude",
        "--mapping",
        mapping,
    ]
    assert loadout.main([*args, "--yes", "--json"]) == 2
    assert "source-conflict" in {i["code"] for i in json.loads(capsys.readouterr().out)["issues"]}
    assert (source / "settings.json").read_text() == '{"model":"source"}\n'
    selection = json.dumps({"source": str(source), "destination": str(live)})
    assert loadout.main([*args, "--select-source", selection, "--yes"]) == 0
    assert json.loads((live / "settings.json").read_text())["model"] == "source"
    assert not (source / "settings.json").exists()


def test_existing_source_registration_is_an_explicit_transaction(tmp_path, capsys):
    _repo(tmp_path)
    assert loadout.main(["init", "global", str(tmp_path), "--harness", "claude", "--yes"]) == 0
    machine = machine_config_path()
    machine.write_text('source = "/different"\n')
    capsys.readouterr()
    args = ["init", "global", str(tmp_path), "--yes", "--json"]
    assert loadout.main(args) == 2
    assert json.loads(capsys.readouterr().out)["issues"][0]["code"] == "registration-conflict"
    assert loadout.main([*args, "--registration", "keep"]) == 0
    assert machine.read_text() == 'source = "/different"\n'
    capsys.readouterr()
    assert loadout.main([*args, "--registration", "replace"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert str(machine) in result["written"]
    assert tomllib.loads(machine.read_text())["source"] == str(tmp_path / "loadout")


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt])
def test_global_apply_cancellation_does_not_mutate(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def cancel(prompt):
        raise failure

    monkeypatch.setattr("builtins.input", cancel)
    assert loadout.main(["init", "global", str(tmp_path), "--harness", "claude"]) == 0
    assert list(tmp_path.iterdir()) == []
    assert not machine_config_path().exists()


def test_interactive_agent_choice_then_decline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter(("claude", "n"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    assert loadout.main(["init", "project", "--root", str(tmp_path)]) == 0
    assert "declined" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_noninteractive_apply_requires_explicit_approval(tmp_path, capsys):
    assert loadout.main(["init", "project", "--harness", "claude", "--root", str(tmp_path)]) == 2
    assert "--yes" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("kind", [[], {}, 1])
def test_invalid_mapping_shape_is_usage_error(tmp_path, capsys, kind):
    mapping = json.dumps(
        {"source": str(tmp_path), "destination": str(tmp_path), "agents": ["claude"], "kind": kind}
    )
    assert loadout.main(["init", "project", "--mapping", mapping]) == 2
    assert "mapping kind" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["resume", "recover"])
def test_cli_resume_and_recover_use_the_reported_journal(tmp_path, monkeypatch, capsys, action):
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("original rules\n")

    def finish_fails(*args, **kwargs):
        raise LoadoutError("staging interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(migration_transaction, "_finish", finish_fails)
        assert (
            loadout.main(
                [
                    "init",
                    "project",
                    "--root",
                    str(tmp_path),
                    "--harness",
                    "claude",
                    "--yes",
                    "--json",
                ]
            )
            == 1
        )
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "interrupted"
    assert (tmp_path / "loadout/config.toml").exists()
    assert loadout.main(["init", action, failure["journal"], "--yes", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("complete" if action == "resume" else "recovered")
    assert result["baseline"] == _git(tmp_path, "rev-parse", "HEAD")
    assert result["journal"] is None
    assert not Path(failure["journal"]).parent.exists()
    assert (tmp_path / "CLAUDE.md").read_text() == "original rules\n"
    assert (tmp_path / "loadout/config.toml").exists() == (action == "resume")
    assert loadout.main(["init", action, failure["journal"], "--yes", "--json"]) == 3
    missing = capsys.readouterr()
    assert "missing or unreadable migration journal" in missing.err
    assert json.loads(missing.out) == {"status": "error", "complete": False, "exit_code": 3}


def test_cli_recovery_conflicts_report_paths_and_nonzero_status(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)

    def finish_fails(*args, **kwargs):
        raise LoadoutError("staging interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(migration_transaction, "_finish", finish_fails)
        assert (
            loadout.main(
                [
                    "init",
                    "project",
                    "--root",
                    str(tmp_path),
                    "--harness",
                    "claude",
                    "--yes",
                    "--json",
                ]
            )
            == 1
        )
    result = json.loads(capsys.readouterr().out)
    source = tmp_path / "loadout/config.toml"
    source.write_text("user changed source\n")
    assert loadout.main(["init", "recover", result["journal"], "--yes", "--json"]) == 1
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["status"] == "recovery-conflicts"
    assert str(source) in recovered["conflicts"]
    assert source.read_text() == "user changed source\n"


def test_project_init_does_not_adopt_global_live_state(tmp_path, fake_home, capsys):
    global_config = fake_home / ".claude/settings.json"
    global_config.parent.mkdir()
    global_config.write_text('{"model":"global"}')
    assert (
        loadout.main(
            [
                "init",
                "project",
                "--harness",
                "claude",
                "--root",
                str(tmp_path),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["scope"] == "project"
    assert preview["candidates"] == []
    assert global_config.read_text() == '{"model":"global"}'


def test_preparation_error_keeps_json_parseable(tmp_path, capsys):
    missing = tmp_path / "missing"
    assert (
        loadout.main(
            [
                "init",
                "project",
                "--root",
                str(missing),
                "--harness",
                "claude",
                "--dry-run",
                "--json",
            ]
        )
        == 3
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"status": "error", "complete": False, "exit_code": 3}
    assert str(missing) in captured.err


def test_existing_mixed_manifest_includes_legacy_skill_membership(tmp_path, capsys):
    (tmp_path / "artifacts.toml").write_text("artifact = []\n")
    (tmp_path / "loadout.toml").write_text(
        'artifacts = "artifacts.toml"\n[[source]]\nname = "skills"\npath = "."\nuse = ["skills"]\n\n[pi]\npermissions = false\nmcp = false\nmodule-config = false\n'
    )
    assert (
        loadout.main(
            [
                "init",
                "global",
                str(tmp_path),
                "--harness",
                "pi",
                "--registration",
                "keep",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["agents"] == ["pi"]
    assert preview["already_initialized"]


def test_global_init_with_an_existing_skill_leaves_sync_working(tmp_path, fake_home, capsys):
    """Adopting a skill records a receipt for a path no artifact route covers.

    Regression: the catalog receipt made sync treat the file it was writing as a
    retired deployment, so the first sync after `init global` failed with exit 3.
    """
    _repo(tmp_path)
    skill = fake_home / ".claude/skills/code-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review changes.\n---\n\nBody.\n", encoding="utf-8"
    )
    (fake_home / ".claude/CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")

    assert loadout.main(["init", "global", str(tmp_path), "--yes"]) == 0
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert (fake_home / ".claude/skills/code-review/SKILL.md").is_file()
    assert loadout.main(["check", "--global"]) == 0


def test_a_hook_another_tool_installs_survives_the_next_global_sync(
    tmp_path, fake_home, monkeypatch, capsys
):
    """The D-02 regression. `init global` stages the source without committing it, so
    the git guard had no baseline, and the second sync stripped the hook with exit 0."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    claude = fake_home / ".claude"
    (claude / "skills" / "review").mkdir(parents=True)
    (claude / "CLAUDE.md").write_text("# global\n")
    (claude / "settings.json").write_text(
        '{"model": "opus", "permissions": {"allow": ["Bash(ls:*)"]}}\n'
    )
    (claude / "skills" / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: r\n---\nbody\n"
    )
    source = (tmp_path / "src").resolve()
    source.mkdir()
    init = ["init", "global", "--harness", "claude", str(source), "--yes"]
    assert loadout.main(init) == 0
    assert loadout.main(["sync", "--global"]) == 0
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, check=False
    )
    assert head.returncode != 0
    settings = claude / "settings.json"
    hook = [{"hooks": [{"type": "command", "command": "echo compacting"}]}]
    document = json.loads(settings.read_text())
    document["hooks"] = {"PreCompact": hook}
    settings.write_text(json.dumps(document, indent=2) + "\n")
    capsys.readouterr()

    assert loadout.main(["sync", "--global"]) == 1

    assert "settings.json was modified outside loadout" in capsys.readouterr().err
    assert json.loads(settings.read_text())["hooks"]["PreCompact"] == hook


def _ordinary_claude_home(home: Path) -> None:
    claude = home / ".claude"
    for name in ("review", "explain"):
        (claude / "skills" / name).mkdir(parents=True)
        (claude / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\nbody\n"
        )
    (claude / "CLAUDE.md").write_text("# global\n")
    (claude / "settings.json").write_text('{"model": "opus"}\n')


def _home_files(home: Path) -> dict[Path, bytes]:
    return {
        path: path.read_bytes()
        for path in home.rglob("*")
        if path.is_file() and ".config" not in path.relative_to(home).parts
    }


def test_global_init_seeds_servers_and_one_readme_and_nothing_else(tmp_path, fake_home, capsys):
    _repo(tmp_path)
    _ordinary_claude_home(fake_home)

    assert loadout.main(["init", "global", str(tmp_path), "--yes"]) == 0

    source = tmp_path / "loadout"
    assert tomllib.loads((source / "mcp.toml").read_text()) == {}
    assert "[context7]" in (source / "mcp.toml").read_text()
    assert "## Add hooks, plugins or settings" in (source / "README.md").read_text()
    assert "mcp" in tomllib.loads((source / "loadout.toml").read_text())["source"][0]["use"]
    assert sorted(p.name for p in tmp_path.rglob("README.md")) == ["README.md"]
    assert list(tmp_path.rglob(".gitkeep")) == []

    assert loadout.main(["sync", "--global"]) == 0
    synced = _home_files(fake_home)
    assert loadout.main(["sync", "--global"]) == 0
    assert _home_files(fake_home) == synced


def test_a_comment_only_mcp_toml_renders_exactly_as_none(tmp_path, fake_home):
    _repo(tmp_path)
    _ordinary_claude_home(fake_home)
    assert loadout.main(["init", "global", str(tmp_path), "--yes"]) == 0
    source = tmp_path / "loadout"
    seeded = render_all(source)

    (source / "mcp.toml").unlink()
    manifest = source / "loadout.toml"
    manifest.write_text(manifest.read_text().replace('"mcp", ', ""))

    assert "mcp" not in tomllib.loads(manifest.read_text())["source"][0]["use"]
    assert fake_home / ".claude.json" in seeded
    assert render_all(source) == seeded


def test_project_init_seeds_neither_servers_nor_readme(tmp_path, fake_home):
    _repo(tmp_path)
    skill = tmp_path / ".claude" / "skills" / "local"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: local\ndescription: d\n---\nbody\n")

    init = ["init", "project", "--root", str(tmp_path), "--harness", "claude", "--yes"]
    assert loadout.main(init) == 0

    source = tmp_path / "loadout"
    assert (source / "skills").is_dir()
    assert sorted(p.name for p in source.iterdir() if p.is_file()) == [
        "config.toml",
        "permissions.toml",
    ]
    assert list(tmp_path.rglob(".gitkeep")) == []


def test_an_exact_claude_rule_adopts_into_shell_and_renders_back_unchanged(
    tmp_path, fake_home, capsys
):
    _repo(tmp_path)
    claude = fake_home / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(
        '{"permissions": {"allow": ["Bash(git status)", "Bash(ls:*)"]}}\n'
    )

    init = ["init", "global", "--harness", "claude", str(tmp_path), "--yes"]
    assert loadout.main(init) == 0

    source = tomllib.loads((tmp_path / "loadout" / "permissions.toml").read_text())
    assert source["shell"]["allow"] == ["=git status", "ls"]
    assert source["claude"]["extra"]["allow"] == []
    assert loadout.main(["sync", "--global"]) == 0
    settings = json.loads((claude / "settings.json").read_text())
    assert settings["permissions"]["allow"] == ["Bash(git status)", "Bash(ls:*)"]


def test_a_bundled_template_is_applied_after_init_with_template_add(tmp_path, capsys):
    _repo(tmp_path)
    init = ["init", "project", "--harness", "claude", "--root", str(tmp_path), "--yes"]
    assert loadout.main(init) == 0
    root = ["--root", str(tmp_path)]
    assert loadout.main(["template", "add", "backend", "--vendored", *root]) == 0
    assert loadout.main(["sync", *root]) == 0

    vendored = (tmp_path / "loadout" / "templates" / "backend" / "instructions.md").read_text()
    assert vendored.strip() in (tmp_path / "CLAUDE.md").read_text()


def _bytes(*roots):
    return {
        path: path.read_bytes()
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    }


def test_project_init_renders_and_a_following_sync_changes_nothing(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Project rules\n\nBe careful.\n")

    init = ["init", "project", "--harness", "claude", "--root", str(tmp_path), "--yes"]
    assert loadout.main(init) == 0

    assert "loadout reproduces your setup exactly" in capsys.readouterr().out
    claude = (tmp_path / "CLAUDE.md").read_text()
    assert claude.startswith("<!-- Generated by loadout")
    assert "Be careful." in claude
    rendered = _bytes(tmp_path)
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    assert _bytes(tmp_path) == rendered


def test_a_render_that_changes_a_file_shows_the_diff_and_needs_approval(tmp_path, capsys):
    _repo(tmp_path)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"permissions": {"allow": ["Bash(ls:*)"]}}\n')
    init = ["init", "project", "--harness", "claude", "--root", str(tmp_path)]

    assert loadout.main([*init, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Render changes 1 existing file(s)" in out
    assert '+    "ask": [],' in out
    assert loadout.main(init) == 2
    assert settings.read_text() == '{"permissions": {"allow": ["Bash(ls:*)"]}}\n'

    assert loadout.main([*init, "--yes"]) == 0
    assert json.loads(settings.read_text())["permissions"] == {
        "allow": ["Bash(ls:*)"],
        "ask": [],
        "deny": [],
    }


def test_the_json_preview_classifies_each_rendered_output(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Project rules\n")
    init = ["init", "project", "--harness", "claude", "--root", str(tmp_path)]

    assert loadout.main([*init, "--dry-run", "--json"]) == 0

    changes = {
        Path(o["path"]).name: o["change"] for o in json.loads(capsys.readouterr().out)["outputs"]
    }
    assert changes["CLAUDE.md"] == "identical"
    assert changes["mcp-permissions.json"] == "create"


def test_global_init_renders_and_records_so_sync_changes_nothing(
    tmp_path, fake_home, monkeypatch, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _ordinary_claude_home(fake_home)
    (tmp_path / "src").mkdir()
    _repo(tmp_path / "src")

    assert (
        loadout.main(["init", "global", str(tmp_path / "src"), "--harness", "claude", "--yes"]) == 0
    )

    assert written_state_path((tmp_path / "src" / "loadout").resolve()).is_file()
    rendered = _bytes(fake_home, tmp_path / "src")
    assert loadout.main(["sync", "--global"]) == 0
    assert _bytes(fake_home, tmp_path / "src") == rendered


def test_project_init_ignores_the_adopted_skill_folder_not_the_directory(tmp_path):
    _repo(tmp_path)
    skill = tmp_path / ".claude" / "skills" / "local"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: local\ndescription: d\n---\nbody\n")

    init = ["init", "project", "--harness", "claude", "--root", str(tmp_path), "--yes"]
    assert loadout.main(init) == 0

    ignored = (tmp_path / ".gitignore").read_text().splitlines()
    assert "/.claude/skills/local/" in ignored
    assert "/.claude/skills" not in ignored


def test_global_init_writes_every_skill_in_directory_form(tmp_path, fake_home):
    _repo(tmp_path)
    skills = fake_home / ".claude" / "skills"
    (skills / "review").mkdir(parents=True)
    (skills / "review" / "SKILL.md").write_text("---\nname: review\ndescription: r\n---\nbody\n")
    (skills / "deploy" / "scripts").mkdir(parents=True)
    (skills / "deploy" / "SKILL.md").write_text("---\nname: deploy\ndescription: d\n---\nbody\n")
    (skills / "deploy" / "scripts" / "ship.sh").write_text("#!/bin/sh\n")

    assert loadout.main(["init", "global", str(tmp_path), "--harness", "claude", "--yes"]) == 0

    source = tmp_path / "loadout" / "skills"
    assert (source / "review" / "SKILL.md").is_file()
    assert (source / "deploy" / "SKILL.md").is_file()
    assert (source / "deploy" / "scripts" / "ship.sh").is_file()
    assert sorted(p.name for p in source.iterdir() if p.is_file()) == []


def test_global_init_under_a_symlinked_home_prefix_adopts_and_registers(
    tmp_path, monkeypatch, capsys
):
    """The macOS /tmp case: HOME, and the machine config beneath it, sit under a link."""
    real = tmp_path / "real"
    (real / "home" / ".claude").mkdir(parents=True)
    (real / "home" / ".claude" / "CLAUDE.md").write_text("# global\n")
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("HOME", str(alias / "home"))
    (real / "src").mkdir()

    init = ["init", "global", str(alias / "src"), "--harness", "claude", "--yes"]
    assert loadout.main(init) == 0

    assert alias.readlink() == real
    assert (real / "home/.config/loadout/config.toml").is_file()
    assert (real / "src/loadout/instructions").is_dir()
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert "Up to date." in capsys.readouterr().out
