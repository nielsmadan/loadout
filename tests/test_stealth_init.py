from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

import loadout
from loadout import stealth_init
from loadout.discovery import detected_global_harnesses
from loadout.machine import config_dir, machine_config_path
from loadout.stealth_registry import load_registry, stealth_path

pytestmark = pytest.mark.integration

UVX = "uvx --python 3.13 --from git+https://github.com/nielsmadan/loadout.git@v0.10.0"


@pytest.fixture(autouse=True)
def isolated_git(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fake_home / "test-gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _repo(home: Path, name: str) -> Path:
    root = home / "work" / name
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    (root / "AGENTS.md").write_text("Team instructions.\n", encoding="utf-8")
    _git(root, "add", "AGENTS.md")
    _git(root, "commit", "-q", "-m", "team")
    return root.resolve()


def _detect_claude(home: Path) -> None:
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text("{}\n", encoding="utf-8")


def _manifest(folder: Path) -> str:
    return (folder / "loadout.toml").read_text(encoding="utf-8")


def _registered() -> dict[str, Path]:
    return {found.name: found.folder for found in load_registry(stealth_path())}


def test_init_writes_a_minimal_manifest_and_registers_the_folder(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(fake_home, "app1")
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/app*", "--harness", "claude", "--yes"]
    )

    assert code == 0
    assert _manifest(folder) == (
        '[stealth]\ntargets = ["~/work/app*"]\n\n[[source]]\nname = "work"\npath = "."\n\n[claude]\n'
    )
    assert _registered() == {"work": folder.resolve()}
    out = capsys.readouterr().out
    assert f"stealth work: targets matching now:\n  ~/work/{root.name}\n" in out
    assert out.endswith("then: loadout sync --stealth work\n")


def test_init_writes_every_target_and_harness_in_harness_order(fake_home: Path) -> None:
    _repo(fake_home, "app1")
    folder = fake_home / "cfg" / "several"

    code = loadout.main(
        [
            "stealth",
            "init",
            str(folder),
            "--targets",
            "~/work/app*",
            "--targets",
            "/srv/api",
            "--name",
            "mine",
            "--harness",
            "pi",
            "--harness",
            "codex",
            "--harness",
            "claude",
            "--yes",
        ]
    )

    assert code == 0
    assert _manifest(folder) == (
        '[stealth]\ntargets = ["~/work/app*", "/srv/api"]\n\n'
        '[[source]]\nname = "mine"\npath = "."\n\n'
        "[claude]\n[codex]\n[pi]\n"
    )
    assert _registered() == {"mine": folder.resolve()}


def test_an_absolute_target_under_home_is_written_with_a_tilde(fake_home: Path) -> None:
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        [
            "stealth",
            "init",
            str(folder),
            "--targets",
            f"{fake_home}/work/app*",
            "--harness",
            "codex",
            "--yes",
        ]
    )

    assert code == 0
    assert 'targets = ["~/work/app*"]\n' in _manifest(folder)


def test_a_folder_with_a_manifest_is_refused_and_left_byte_identical(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "work"
    folder.mkdir(parents=True)
    original = b"[claude]\ninstructions = ['mine']\n"
    (folder / "loadout.toml").write_bytes(original)

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert (folder / "loadout.toml").read_bytes() == original
    assert "already exists, and stealth init never overwrites one" in capsys.readouterr().err
    assert _registered() == {}


def test_a_folder_inside_a_target_is_refused_and_not_created(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _repo(fake_home, "app1")
    folder = root / "cfg"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        f"loadout: ~/work/app1: a target cannot hold the stealth folder {folder}\n"
    )
    assert sorted(path.name for path in root.iterdir()) == [".git", "AGENTS.md"]


def test_the_home_folder_is_refused_as_a_stealth_folder(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = loadout.main(
        ["stealth", "init", str(fake_home), "--targets", "~/work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert capsys.readouterr().err == "loadout: ~: a stealth folder cannot be the home folder\n"
    assert _registered() == {}


def test_a_relative_target_is_refused_as_the_stealth_table_would_be(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        f"loadout: {folder.resolve()}/loadout.toml: [stealth] targets must be a non-empty list "
        'of absolute or "~/" patterns\n'
    )
    assert _registered() == {}


def test_missing_targets_exits_2_with_an_example(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "work"

    assert loadout.main(["stealth", "init", str(folder), "--yes"]) == 2

    assert capsys.readouterr().err == (
        "loadout: stealth init needs --targets, once for each pattern; for example: "
        'loadout stealth init --targets "~/work/app*"\n'
    )
    assert _registered() == {}


def test_without_a_terminal_it_needs_yes_and_writes_nothing(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude"]
    )

    assert code == 2
    assert capsys.readouterr().err == (
        "loadout: stealth init needs --yes when input is not interactive\n"
    )
    assert _registered() == {}
    assert (fake_home / "cfg").is_dir() is False


@pytest.mark.parametrize(("answer", "registered"), [("n", {}), ("y", {"work": "cfg/work"})])
def test_at_a_terminal_it_asks_before_writing(
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answer: str,
    registered: dict[str, str],
) -> None:
    folder = fake_home / "cfg" / "work"
    prompts: list[str] = []
    monkeypatch.setattr(stealth_init, "interactive", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or answer)

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude"]
    )

    assert code == 0
    assert prompts == [
        (
            f"create {folder.resolve()}/loadout.toml and register stealth source work, which "
            "writes into every checkout its targets match? [y/N] "
        )
    ]
    assert _registered() == {
        name: (fake_home / path).resolve() for name, path in registered.items()
    }
    assert (folder / "loadout.toml").is_file() is (answer == "y")
    assert ("declined; no changes made" in capsys.readouterr().out) is (answer == "n")


def test_a_0_10_0_overlay_registry_refuses_init_with_the_stealth_add_message(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    registry = config_dir() / "overlays.toml"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text('[overlay.work]\npath = "/somewhere"\n', encoding="utf-8")
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        f"loadout: {registry} still registers the 0.10.0 overlay work; remove it first with "
        f"`{UVX} loadout overlay remove work --yes`\n"
    )
    assert folder.exists() is False


def test_a_flagged_harness_wins_over_a_detected_one(fake_home: Path) -> None:
    _detect_claude(fake_home)
    assert detected_global_harnesses(fake_home, {"HOME": str(fake_home)}) == ("claude",)
    folder = fake_home / "cfg" / "work"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "codex", "--yes"]
    )

    assert code == 0
    assert _manifest(folder).endswith('path = "."\n\n[codex]\n')


def test_without_a_flag_a_detected_harness_is_written(fake_home: Path) -> None:
    _detect_claude(fake_home)
    folder = fake_home / "cfg" / "work"

    assert loadout.main(["stealth", "init", str(folder), "--targets", "~/work/*", "--yes"]) == 0

    assert _manifest(folder).endswith('path = "."\n\n[claude]\n')


def test_machine_defaults_win_over_detection(fake_home: Path) -> None:
    _detect_claude(fake_home)
    source = fake_home / "global"
    source.mkdir()
    machine_config_path().parent.mkdir(parents=True, exist_ok=True)
    machine_config_path().write_text(
        f'source = "{source}"\nharnesses = ["pi", "droid"]\n', encoding="utf-8"
    )
    folder = fake_home / "cfg" / "work"

    assert loadout.main(["stealth", "init", str(folder), "--targets", "~/work/*", "--yes"]) == 0

    assert _manifest(folder).endswith('path = "."\n\n[droid]\n[pi]\n')


def test_no_harness_configured_or_detected_exits_2(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "work"

    assert loadout.main(["stealth", "init", str(folder), "--targets", "~/work/*", "--yes"]) == 2

    assert "pass --harness NAME, once for each" in capsys.readouterr().err
    assert folder.exists() is False


def test_init_then_sync_renders_an_added_server_into_the_target(fake_home: Path) -> None:
    root = _repo(fake_home, "app1")
    folder = fake_home / "cfg" / "figma"
    assert (
        loadout.main(
            [
                "stealth",
                "init",
                str(folder),
                "--targets",
                "~/work/app*",
                "--harness",
                "claude",
                "--yes",
            ]
        )
        == 0
    )
    (folder / "mcp.toml").write_text(
        '[figma]\ntransport = "http"\nurl = "https://mcp.figma.com/mcp"\n', encoding="utf-8"
    )

    assert loadout.main(["check", "--stealth", "figma"]) == 1
    assert loadout.main(["sync", "--stealth", "figma"]) == 0
    assert loadout.main(["check", "--stealth", "figma"]) == 0

    claude = json.loads((fake_home / ".claude.json").read_text(encoding="utf-8"))
    assert claude["projects"][str(root)]["mcpServers"] == {
        "figma": {"type": "http", "url": "https://mcp.figma.com/mcp"}
    }
    assert _git(root, "status", "--short") == ""


def test_registration_refusals_stop_init_before_it_writes(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = fake_home / "cfg" / "work"
    argv = ["--targets", "~/work/*", "--harness", "claude", "--yes"]
    assert loadout.main(["stealth", "init", str(first), *argv]) == 0
    second = fake_home / "other" / "work"
    capsys.readouterr()

    assert loadout.main(["stealth", "init", str(second), *argv]) == 3

    assert capsys.readouterr().err == (
        "loadout: a stealth source named work is already registered; pass --name\n"
    )
    assert second.exists() is False


@pytest.fixture
def read_only_config(fake_home: Path) -> Iterator[Path]:
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o500)
    yield directory
    directory.chmod(0o700)


def test_a_failed_registration_removes_every_folder_init_created(
    fake_home: Path, read_only_config: Path
) -> None:
    folder = fake_home / "cfg" / "q1" / "q2" / "q3"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/work/*", "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert (fake_home / "cfg").exists() is False


def test_a_failed_registration_keeps_the_folders_that_already_existed(
    fake_home: Path, read_only_config: Path
) -> None:
    kept = fake_home / "cfg" / "q1"
    kept.mkdir(parents=True)

    code = loadout.main(
        [
            "stealth",
            "init",
            str(kept / "q2" / "q3"),
            "--targets",
            "~/work/*",
            "--harness",
            "claude",
            "--yes",
        ]
    )

    assert code == 3
    assert kept.is_dir()
    assert list(kept.iterdir()) == []


def test_a_target_with_a_control_character_is_refused_before_anything_is_written(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "del1"

    code = loadout.main(
        [
            "stealth",
            "init",
            str(folder),
            "--targets",
            "~/work/a\x7fb",
            "--harness",
            "claude",
            "--yes",
        ]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        f"loadout: {folder.resolve()}/loadout.toml: [stealth] targets: the pattern "
        "'~/work/a\\x7fb' holds a control character or a byte that is not UTF-8, which a target "
        "path cannot use\n"
    )
    assert (fake_home / "cfg").exists() is False


def test_stealth_add_refuses_a_hand_written_target_with_a_control_character(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "del1"
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(
        '[stealth]\ntargets = ["~/work/a\\u007fb"]\n\n[[source]]\nname = "del1"\npath = "."\n\n'
        "[claude]\n",
        encoding="utf-8",
    )

    assert loadout.main(["stealth", "add", str(folder)]) == 3

    assert "the pattern '~/work/a\\x7fb' holds a control character" in capsys.readouterr().err
    assert _registered() == {}


@pytest.mark.parametrize("pattern", ["~/cfg/z/sub*", "~/cfg/z/one", "~/cfg/z/*/deep*"])
def test_init_refuses_a_pattern_that_can_only_match_inside_the_folder(
    fake_home: Path, capsys: pytest.CaptureFixture[str], pattern: str
) -> None:
    folder = fake_home / "cfg" / "z"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", pattern, "--harness", "claude", "--yes"]
    )

    assert code == 3
    assert capsys.readouterr().err == (
        f"loadout: the pattern {pattern} can match only inside the stealth folder "
        f"{folder.resolve()}, and a target cannot be inside the stealth folder\n"
    )
    assert (fake_home / "cfg").exists() is False


def test_stealth_add_refuses_a_pattern_that_can_only_match_inside_the_folder(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = fake_home / "cfg" / "z"
    folder.mkdir(parents=True)
    (folder / "loadout.toml").write_text(
        '[stealth]\ntargets = ["~/work/*", "~/cfg/z/sub*"]\n\n[[source]]\nname = "z"\n'
        'path = "."\n\n[claude]\n',
        encoding="utf-8",
    )

    assert loadout.main(["stealth", "add", str(folder)]) == 3

    assert capsys.readouterr().err == (
        f"loadout: the pattern ~/cfg/z/sub* can match only inside the stealth folder "
        f"{folder.resolve()}, and a target cannot be inside the stealth folder\n"
    )
    assert _registered() == {}


def test_a_pattern_whose_wildcard_reaches_above_the_folder_is_accepted(fake_home: Path) -> None:
    folder = fake_home / "cfg" / "z"

    code = loadout.main(
        ["stealth", "init", str(folder), "--targets", "~/cfg/*", "--harness", "claude", "--yes"]
    )

    assert code == 0
    assert _registered() == {"z": folder.resolve()}


def test_a_typo_in_the_permissions_table_stops_check_after_init(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(fake_home, "app1")
    folder = fake_home / "cfg" / "work"
    argv = ["--targets", "~/work/app*", "--harness", "claude", "--yes"]
    assert loadout.main(["stealth", "init", str(folder), *argv]) == 0
    (folder / "permissions.toml").write_text('[shel]\ndeny = ["git push"]\n', encoding="utf-8")
    capsys.readouterr()

    assert loadout.main(["check", "--stealth", "work"]) == 3

    assert (
        f"{folder.resolve()}/permissions.toml: unknown key(s) or table(s) shel (allowed: [shell], "
        "[mcp], [claude.extra] and [opencode.extra])"
    ) in capsys.readouterr().err
