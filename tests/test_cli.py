from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.errors import LoadoutError


def _write_machine_config(xdg_home: Path, source: Path, profile: str | None = None) -> Path:
    config_path = xdg_home / "loadout" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'source = "{source}"']
    if profile is not None:
        lines.append(f'profile = "{profile}"')
    config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_path


def test_version_is_exposed() -> None:
    assert isinstance(loadout.__version__, str)
    assert loadout.__version__


def test_no_args_prints_usage_and_returns_2(capsys) -> None:
    assert loadout.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_sync_writes_files_and_returns_0(root: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert (root / "global" / "AGENTS.md").is_file()
    assert "AGENTS.md" in capsys.readouterr().out


def test_sync_default_profile_omits_the_autonomous_target(root: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert (root / "claude" / "CLAUDE.md").is_file()
    assert not (root / "claude" / "CLAUDE.autonomous.md").is_file()


def test_sync_profile_autonomous_selects_the_autonomous_target(root: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(root), "--profile", "autonomous"]) == 0
    assert (root / "claude" / "CLAUDE.autonomous.md").is_file()
    # instructions.claude declares profile = "default", so it is mutually exclusive
    # with instructions.claude-autonomous — both share the ~/.claude/CLAUDE.md
    # destination, and only one of them may be selected at a time.
    assert not (root / "claude" / "CLAUDE.md").is_file()
    # The real-world case profile filtering exists for: on a machine running the
    # autonomous profile, every target that does NOT declare a profile must still
    # render. Codex, OpenCode, Pi and Antigravity have no autonomous variant and
    # must never disappear when the active profile changes.
    unprofiled_outputs = (
        "global/AGENTS.md",
        "antigravity/settings.json",
        "claude/settings.json",
        "claude/settings.autonomous.json",
        "claude/mcp-permissions.json",
        "codex/rules/permissions.rules",
        "codex/mcp-permissions.toml",
        "opencode/opencode.json",
        "pi/permissions.json",
    )
    for output in unprofiled_outputs:
        assert (root / output).is_file(), output


def test_check_returns_0_when_clean(root: Path) -> None:
    loadout.main(["sync", "--root", str(root)])
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_check_returns_1_and_diffs_on_drift(root: Path, capsys) -> None:
    loadout.main(["sync", "--root", str(root)])
    (root / "global" / "AGENTS.md").write_text("tampered\n")
    assert loadout.main(["check", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "tampered" in err


def test_check_drift_depends_on_the_active_profile(root: Path, capsys) -> None:
    """sync --root under the default profile never writes the autonomous-only target,
    so check must be clean under 'default' but report drift once --profile autonomous
    puts that target back in the render set."""
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert not (root / "claude" / "CLAUDE.autonomous.md").exists()
    assert loadout.main(["check", "--root", str(root)]) == 0
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root), "--profile", "autonomous"]) == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "claude/CLAUDE.autonomous.md" in err


def test_missing_manifest_returns_3(tmp_path: Path, capsys) -> None:
    assert loadout.main(["sync", "--root", str(tmp_path)]) == 3
    assert "manifest" in capsys.readouterr().err


def test_missing_fragment_file_returns_3(root: Path, capsys) -> None:
    (root / "instructions" / "secrets.md").unlink()
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "secrets" in capsys.readouterr().err


def test_unexpected_exception_returns_4_with_traceback(root: Path, monkeypatch, capsys) -> None:
    def boom(_root: Path, profile: str) -> int:
        raise ValueError("kaboom")

    monkeypatch.setattr(loadout.cli, "cmd_sync", boom)
    assert loadout.main(["sync", "--root", str(root)]) == 4
    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "ValueError: kaboom" in err


def test_loadout_error_still_returns_3(root: Path, monkeypatch, capsys) -> None:
    def fail(_root: Path, profile: str) -> int:
        raise LoadoutError("deliberate failure")

    monkeypatch.setattr(loadout.cli, "cmd_sync", fail)
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "deliberate failure" in capsys.readouterr().err


def test_explain_reports_the_source_and_path(root: Path, capsys) -> None:
    assert loadout.main(["explain", "web-fetching", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "web-fetching" in out
    assert "source: ac" in out


def test_explain_lists_targets_that_use_the_fragment(root: Path, capsys) -> None:
    assert loadout.main(["explain", "git-policy", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "claude/CLAUDE.md" in out
    assert "claude/CLAUDE.autonomous.md" not in out


def test_explain_survives_an_unrelated_unresolvable_fragment(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"working-style"', '"no-such-fragment"', 1),
        encoding="utf-8",
    )
    assert loadout.main(["explain", "git-policy", "--root", str(root)]) == 0
    captured = capsys.readouterr()
    assert "used by:" in captured.out
    assert "no-such-fragment" in captured.err


def test_explain_finds_users_when_queried_by_qualified_name(root: Path, capsys) -> None:
    assert loadout.main(["explain", "ac/git-policy", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "claude/CLAUDE.md" in out
    assert "no target lists it" not in out


def test_explain_on_unknown_name_returns_3(root: Path, capsys) -> None:
    assert loadout.main(["explain", "nope", "--root", str(root)]) == 3
    assert "nope" in capsys.readouterr().err


def test_sync_with_unknown_fragment_returns_3(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"intro-claude"', '"no-such-fragment"'),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "no-such-fragment" in capsys.readouterr().err


def test_sync_with_ambiguous_fragment_returns_3(root: Path, capsys) -> None:
    second = root / "second" / "instructions"
    second.mkdir(parents=True)
    (second / "web-fetching.md").write_text("duplicate\n", encoding="utf-8")
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '[[source]]\nname = "ac"\npath = "."',
            '[[source]]\nname = "ac"\npath = "."\n\n[[source]]\nname = "second"\npath = "second"',
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    err = capsys.readouterr().err
    assert "ac/web-fetching" in err
    assert "second/web-fetching" in err


def test_source_as_a_plain_array_returns_3_not_4(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            '[[source]]\nname = "ac"\npath = "."', 'source = ["a", "b"]'
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "traceback" not in capsys.readouterr().err.lower()


def test_absolute_output_returns_3_and_writes_nothing_outside_root(
    root: Path, tmp_path: Path, capsys
) -> None:
    escape_target = tmp_path.parent / "loadout-escape-absolute.md"
    escape_target.unlink(missing_ok=True)
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'output       = "claude/CLAUDE.md"', f'output       = "{escape_target}"'
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert not escape_target.exists()


def test_empty_output_returns_3_not_4(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'output       = "claude/CLAUDE.md"', 'output       = ""'
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "traceback" not in capsys.readouterr().err.lower()


def test_output_escaping_the_root_returns_3_and_writes_nothing_outside_root(
    root: Path, capsys
) -> None:
    escape_target = root.parent / "escaped.md"
    escape_target.unlink(missing_ok=True)
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'output       = "claude/CLAUDE.md"', 'output       = "../escaped.md"'
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert not escape_target.exists()


def test_zero_targets_of_either_kind_returns_3_not_0(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8")
        .replace("[instructions.claude]", "[instruction.claude]")
        .replace("[instructions.claude-autonomous]", "[instruction.claude-autonomous]")
        .replace("[instructions.shared]", "[instruction.shared]")
        .replace("[permissions.", "[permission."),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    err = capsys.readouterr().err
    assert "target" in err


def test_duplicate_output_across_targets_returns_3_not_0(root: Path, capsys) -> None:
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'output       = "claude/CLAUDE.autonomous.md"', 'output       = "claude/CLAUDE.md"'
        ),
        encoding="utf-8",
    )
    assert loadout.main(["sync", "--root", str(root)]) == 3
    assert "claude/CLAUDE.md" in capsys.readouterr().err


def test_init_sync_check_round_trips_for_a_project_only_repo(tmp_path: Path, capsys) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert (
        loadout.main(
            ["init", "--harness", "claude", "--harness", "opencode", "--root", str(tmp_path)]
        )
        == 0
    )
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
    assert (tmp_path / ".claude" / "settings.json").is_file()
    assert (tmp_path / "opencode.json").is_file()

    assert loadout.main(["check", "--root", str(tmp_path)]) == 0


def test_init_warns_but_succeeds_with_a_tracked_instruction_file_present(
    tmp_path: Path, capsys
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "CLAUDE.md").write_text("# project rules\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "CLAUDE.md"], check=True)

    assert loadout.main(["init", "--harness", "claude", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "CLAUDE.md" in out
    assert (tmp_path / "loadout" / "config.toml").is_file()


def test_init_rejects_duplicate_harnesses(tmp_path: Path, capsys) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert (
        loadout.main(
            ["init", "--harness", "claude", "--harness", "claude", "--root", str(tmp_path)]
        )
        == 3
    )
    assert "duplicate" in capsys.readouterr().err
    assert not (tmp_path / "loadout" / "config.toml").exists()


def test_explain_in_a_project_only_repo_names_both_manifests(tmp_path: Path, capsys) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    loadout.main(["init", "--harness", "claude", "--root", str(tmp_path)])
    capsys.readouterr()

    assert loadout.main(["explain", "foo", "--root", str(tmp_path)]) == 3
    err = capsys.readouterr().err
    assert "loadout.toml" in err
    assert "loadout/config.toml" in err


def test_check_returns_1_when_a_project_output_has_drifted(tmp_path: Path, capsys) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    loadout.main(["init", "--harness", "claude", "--root", str(tmp_path)])
    loadout.main(["sync", "--root", str(tmp_path)])
    capsys.readouterr()

    tampered = tmp_path / ".aiconf" / "mcp-permissions.json"
    tampered.write_text("tampered\n", encoding="utf-8")

    assert loadout.main(["check", "--root", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "DRIFT" in err
    assert "tampered" in err


def test_global_without_a_machine_config_names_init(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert loadout.main(["check", "--global"]) == 3
    err = capsys.readouterr().err
    assert str(tmp_path / "cfg" / "loadout" / "config.toml") in err
    assert "loadout init --global" in err


def test_global_and_root_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as caught:
        loadout.main(["sync", "--global", "--root", "."])
    assert caught.value.code == 2


def test_global_uses_the_configured_source(root: Path, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_machine_config(tmp_path / "xdg", root)

    assert loadout.main(["sync", "--global"]) == 0
    assert (root / "global" / "AGENTS.md").is_file()


def test_global_applies_the_configured_profile(root: Path, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_machine_config(tmp_path / "xdg", root, profile="autonomous")

    assert loadout.main(["sync", "--global"]) == 0
    assert (root / "claude" / "CLAUDE.autonomous.md").is_file()
    assert not (root / "claude" / "CLAUDE.md").is_file()


def test_global_explicit_profile_overrides_the_machine_config(
    root: Path, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_machine_config(tmp_path / "xdg", root, profile="autonomous")

    assert loadout.main(["sync", "--global", "--profile", "default"]) == 0
    assert (root / "claude" / "CLAUDE.md").is_file()
    assert not (root / "claude" / "CLAUDE.autonomous.md").is_file()


def test_global_with_no_configured_profile_falls_back_to_default(
    root: Path, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write_machine_config(tmp_path / "xdg", root)

    assert loadout.main(["sync", "--global"]) == 0
    assert (root / "claude" / "CLAUDE.md").is_file()
    assert not (root / "claude" / "CLAUDE.autonomous.md").is_file()


def test_sync_succeeds_under_a_non_utf8_locale(root: Path, monkeypatch) -> None:
    # Fragments contain non-ASCII characters (em-dash, ellipsis). Under a
    # locale whose default encoding is ASCII, file I/O must still work
    # because loadout always opens files as UTF-8 explicitly.
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "C")
    monkeypatch.setenv("PYTHONCOERCECLOCALE", "0")
    monkeypatch.setenv("PYTHONUTF8", "0")
    result = subprocess.run(
        [sys.executable, "-m", "loadout", "sync", "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "global" / "AGENTS.md").read_text(encoding="utf-8")


class _NoTTY:
    def isatty(self) -> bool:
        return False

    def readline(self, *args: object, **kwargs: object) -> str:
        raise AssertionError("must not read from stdin when it is not a TTY")


def test_init_global_creates_the_source_and_machine_config(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    source_parent = tmp_path / "home"

    assert loadout.main(["init", "--global", "--source", str(source_parent)]) == 0

    out = capsys.readouterr().out
    loadout_dir = source_parent / "loadout"
    assert (loadout_dir / "loadout.toml").is_file()
    assert (tmp_path / "cfg" / "loadout" / "config.toml").is_file()
    assert "sync --global" in out


def test_init_global_without_source_and_without_a_tty_errors(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setattr(sys, "stdin", _NoTTY())

    assert loadout.main(["init", "--global"]) == 3

    err = capsys.readouterr().err
    assert "--source" in err
    assert not (tmp_path / "cfg" / "loadout" / "config.toml").exists()


def test_global_and_harness_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit) as caught:
        loadout.main(["init", "--global", "--harness", "claude"])
    assert caught.value.code == 2


def test_init_without_global_or_harness_still_requires_harness() -> None:
    with pytest.raises(SystemExit) as caught:
        loadout.main(["init"])
    assert caught.value.code == 2


def test_init_global_round_trips_to_sync_no_targets_declared(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The honest test of the state init --global leaves behind: it declares a
    source but no targets, so sync fails with load_manifest's existing,
    actionable error rather than a broken or silently-empty sync."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    source_parent = tmp_path / "home"

    assert loadout.main(["init", "--global", "--source", str(source_parent)]) == 0
    capsys.readouterr()

    assert loadout.main(["sync", "--global"]) == 3

    err = capsys.readouterr().err
    manifest_file = source_parent / "loadout" / "loadout.toml"
    assert str(manifest_file) in err
    assert "no [instructions.<agent>] or [permissions.<name>] targets declared" in err


def test_init_notes_a_missing_machine_config(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    assert loadout.main(["init", "--harness", "claude", "--root", str(tmp_path)]) == 0

    out = capsys.readouterr().out
    assert "init --global" in out
    assert (tmp_path / "loadout" / "config.toml").is_file()
