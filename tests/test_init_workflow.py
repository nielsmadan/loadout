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
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path

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
    assert loadout.main(["init", "--root", str(tmp_path), "--dry-run", "--json", "--yes"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert {i["code"] for i in data["issues"]} == {
        "scope-required",
        "agents-required",
        "source-mapping",
    }
    assert list(tmp_path.iterdir()) == [tmp_path / "AGENTS.md"]


def test_project_preview_and_apply_adopt_and_stage(tmp_path, capsys):
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("# Rules\nKeep this.\n")
    args = ["init", "--project", "--harness", "claude", "--root", str(tmp_path)]
    assert loadout.main([*args, "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["git"]["baseline_paths"] == ["CLAUDE.md"]
    assert preview["stage_paths"]
    assert not (tmp_path / "loadout").exists()
    assert loadout.main([*args, "--yes"]) == 0
    assert (tmp_path / "CLAUDE.md").read_text() == "# Rules\nKeep this.\n"
    assert _git(tmp_path, "show", "HEAD:CLAUDE.md") == "# Rules\nKeep this."
    assert "loadout/config.toml" in _git(tmp_path, "diff", "--cached", "--name-only")
    assert loadout.main(["check", "--root", str(tmp_path)]) == 0
    nested = tmp_path / "src"
    nested.mkdir()
    old_head = _git(tmp_path, "rev-parse", "HEAD")
    assert loadout.main(["init", "--root", str(nested), "--yes", "--json"]) == 0
    assert _git(tmp_path, "rev-parse", "HEAD") == old_head


def test_global_defaults_to_cwd_and_registers_actual_manifest(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "--global", "--harness", "pi", "--yes"]) == 0
    data = tomllib.loads(machine_config_path().read_text())
    assert data == {"source": str(tmp_path / "loadout"), "harnesses": ["pi"]}
    assert loadout.main(["check", "--global"]) == 0
    capsys.readouterr()
    assert loadout.main(["init", "--global", "--yes", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["already_initialized"]


def test_project_init_uses_machine_harness_defaults(tmp_path, fake_home, capsys):
    source = fake_home / "global"
    source.mkdir()
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text(f'source = "{source}"\nharnesses = ["claude", "droid"]\n')

    assert loadout.main(["init", "--project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
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

    assert loadout.main(["init", "--project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["agents"] == ["droid"]


def test_stale_global_source_does_not_block_machine_harness_defaults(tmp_path, capsys):
    machine = machine_config_path()
    machine.parent.mkdir(parents=True)
    machine.write_text('source = "/nope/missing"\nharnesses = ["droid"]\n')

    assert loadout.main(["init", "--project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
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
                "--project",
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
                "--project",
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

    assert loadout.main(["init", "--project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["agents"] == ["claude"]
    assert preview["already_initialized"]


def test_repeat_global_init_saves_defaults_and_preserves_machine_config(
    tmp_path, monkeypatch, capsys
):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "--global", "--harness", "pi", "--yes"]) == 0
    machine = machine_config_path()
    source = tmp_path / "loadout"
    machine.write_text(
        f'# selected here\nsource = "{source}"\nprofile = "autonomous"\nharnesses = ["claude"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["init", "--global", "--yes", "--json"]) == 0
    assert machine.read_text(encoding="utf-8") == (
        f'# selected here\nsource = "{source}"\nprofile = "autonomous"\nharnesses = ["pi"]\n'
    )


def test_matching_registration_keep_preserves_harness_defaults(tmp_path, monkeypatch, capsys):
    _repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert loadout.main(["init", "--global", "--harness", "pi", "--yes"]) == 0
    machine = machine_config_path()
    source = tmp_path / "loadout"
    machine.write_text(
        f'source = "{source}"\nharnesses = ["claude"]\n',
        encoding="utf-8",
    )
    capsys.readouterr()

    assert loadout.main(["init", "--global", "--registration", "keep", "--yes", "--json"]) == 0
    assert tomllib.loads(machine.read_text(encoding="utf-8"))["harnesses"] == ["claude"]


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
        "--global",
        "--source",
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
    assert (
        loadout.main(
            ["init", "--global", "--source", str(tmp_path), "--harness", "claude", "--yes"]
        )
        == 0
    )
    machine = machine_config_path()
    machine.write_text('source = "/different"\n')
    capsys.readouterr()
    args = ["init", "--global", "--source", str(tmp_path), "--yes", "--json"]
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
def test_global_prompt_cancellation_with_yes_does_not_mutate(tmp_path, monkeypatch, failure):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def cancel(prompt):
        raise failure

    monkeypatch.setattr("builtins.input", cancel)
    assert (
        loadout.main(["init", "--global", "--root", str(tmp_path), "--harness", "claude", "--yes"])
        == 0
    )
    assert list(tmp_path.iterdir()) == []
    assert not machine_config_path().exists()


def test_interactive_scope_and_agent_choices_then_decline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter(("project", "claude", "none", "n"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    assert loadout.main(["init", "--root", str(tmp_path)]) == 0
    assert "declined" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_noninteractive_apply_requires_explicit_approval(tmp_path, capsys):
    assert loadout.main(["init", "--project", "--harness", "claude", "--root", str(tmp_path)]) == 2
    assert "--yes" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


def test_native_harness_add_refuses_without_creating_false_membership(tmp_path, capsys):
    _repo(tmp_path)
    assert (
        loadout.main(["init", "--project", "--harness", "claude", "--root", str(tmp_path), "--yes"])
        == 0
    )
    config = tmp_path / "loadout/config.toml"
    before = config.read_bytes()
    nested = tmp_path / "src"
    nested.mkdir()
    assert loadout.main(["harness", "add", "pi", "--root", str(nested)]) == 3
    assert "explicit artifact routes" in capsys.readouterr().err
    assert config.read_bytes() == before


def test_preview_never_prints_secret_values(tmp_path, capsys):
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"env":{"API_KEY":"literal-private-value"}}')
    assert loadout.main(["init", "--project", "--root", str(tmp_path), "--dry-run", "--json"]) == 0
    captured = capsys.readouterr()
    assert "literal-private-value" not in captured.out + captured.err
    assert any(entry["private"] for entry in json.loads(captured.out)["source_writes"])


@pytest.mark.parametrize("kind", [[], {}, 1])
def test_invalid_mapping_shape_is_usage_error(tmp_path, capsys, kind):
    mapping = json.dumps(
        {"source": str(tmp_path), "destination": str(tmp_path), "agents": ["claude"], "kind": kind}
    )
    assert loadout.main(["init", "--mapping", mapping]) == 2
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
                    "--project",
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
    assert loadout.main(["init", f"--{action}", failure["journal"], "--yes", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("complete" if action == "resume" else "recovered")
    assert result["baseline"] == _git(tmp_path, "rev-parse", "HEAD")
    assert result["journal"] is None
    assert not Path(failure["journal"]).parent.exists()
    assert (tmp_path / "CLAUDE.md").read_text() == "original rules\n"
    assert (tmp_path / "loadout/config.toml").exists() == (action == "resume")
    assert loadout.main(["init", f"--{action}", failure["journal"], "--yes", "--json"]) == 3
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
                    "--project",
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
    assert loadout.main(["init", "--recover", result["journal"], "--yes", "--json"]) == 1
    recovered = json.loads(capsys.readouterr().out)
    assert recovered["status"] == "recovery-conflicts"
    assert str(source) in recovered["conflicts"]
    assert source.read_text() == "user changed source\n"


def test_explicit_project_scope_does_not_adopt_global_live_state(tmp_path, fake_home, capsys):
    global_config = fake_home / ".claude/settings.json"
    global_config.parent.mkdir()
    global_config.write_text('{"model":"global"}')
    assert (
        loadout.main(
            [
                "init",
                "--project",
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
                "--project",
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
                "--global",
                "--source",
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
    retired deployment, so the first sync after `init --global` failed with exit 3.
    """
    _repo(tmp_path)
    skill = fake_home / ".claude/skills/code-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: code-review\ndescription: Review changes.\n---\n\nBody.\n", encoding="utf-8"
    )
    (fake_home / ".claude/CLAUDE.md").write_text("# Instructions\n", encoding="utf-8")

    assert loadout.main(["init", "--global", "--source", str(tmp_path), "--yes"]) == 0
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert (fake_home / ".claude/skills/code-review/SKILL.md").is_file()
    assert loadout.main(["check", "--global"]) == 0
