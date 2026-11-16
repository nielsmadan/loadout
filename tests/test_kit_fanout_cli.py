from __future__ import annotations

import json
from pathlib import Path
from shutil import rmtree

import pytest

from loadout import stealth_commands
from loadout.cli import main
from loadout.kits import kit_divergence, tree_hash, vendored_path
from loadout.project import load_project_config, project_config_path

pytestmark = pytest.mark.integration


@pytest.fixture
def source(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This machine's global source, offering the directory kit `web`."""
    root = fake_home / "ac"
    _write(root / "loadout" / "kits" / "web" / "instructions.md", "# Web\n\nUse vite.\n")
    _write(
        root / "loadout.toml",
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
    )
    xdg = fake_home / ".config"
    _write(xdg / "loadout" / "config.toml", f'source = "{root}"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return root


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _kit(source: Path) -> Path:
    return source / "loadout" / "kits" / "web" / "instructions.md"


def _project(tmp_path: Path, name: str, mode: str) -> Path:
    """A project using `web`, synced once so this machine's record names it."""
    root = tmp_path / name
    _write(root / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["kit", "add", "web", f"--{mode}", "--root", str(root)]) == 0
    assert main(["sync", "--root", str(root)]) == 0
    return root


def _instructions(project: Path) -> str:
    return (project / "CLAUDE.md").read_text(encoding="utf-8")


def _copy(project: Path) -> Path:
    return vendored_path(project, "web") / "instructions.md"


def _terminal(monkeypatch: pytest.MonkeyPatch, answer: str | None) -> list[str]:
    """A terminal answering every question with `answer`; None fails the test if asked."""
    asked: list[str] = []

    def ask(question: str) -> str:
        asked.append(question)
        if answer is None:
            raise AssertionError(f"asked without a terminal: {question}")
        return answer

    monkeypatch.setattr(stealth_commands, "interactive", lambda: answer is not None)
    monkeypatch.setattr("builtins.input", ask)
    return asked


def test_a_global_sync_updates_every_project_using_the_kit(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    linked = _project(tmp_path, "linked", "linked")
    vendored = _project(tmp_path, "vendored", "vendored")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    capsys.readouterr()

    assert main(["sync", "--global"]) == 0

    assert "Use vite 7." in _instructions(linked)
    assert "Use vite 7." in _instructions(vendored)
    assert _copy(vendored).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    recorded = load_project_config(project_config_path(vendored)).vendored_hash("web")
    assert recorded == tree_hash(vendored_path(vendored, "web"))
    out = capsys.readouterr().out
    assert f"project {linked}:" in out
    assert (
        f"note: project {vendored}: kit web: updated from its source, 1 file(s); "
        f"commit loadout/ in this project"
    ) in out


def test_a_sync_in_the_global_source_folder_also_updates_the_projects(
    tmp_path: Path, source: Path
) -> None:
    linked = _project(tmp_path, "linked", "linked")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")

    assert main(["sync", "--root", str(source)]) == 0

    assert "Use vite 7." in _instructions(linked)


def test_a_global_sync_leaves_an_edited_vendored_copy_and_says_so(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    edited = _project(tmp_path, "edited", "vendored")
    clean = _project(tmp_path, "clean", "vendored")
    _write(_copy(edited), "# Web\n\nUse webpack.\n")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    capsys.readouterr()

    assert main(["sync", "--global"]) == 0

    assert _copy(edited).read_text(encoding="utf-8") == "# Web\n\nUse webpack.\n"
    assert _copy(clean).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert f"note: project {edited}: kit web: the vendored copy has edits its source" in (
        capsys.readouterr().out
    )


def test_a_project_that_fails_does_not_stop_the_others(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _project(tmp_path, "broken", "linked")
    working = _project(tmp_path, "working", "linked")
    _write(broken / "loadout" / "config.toml", 'harnesses = ["claude"]\nkits = "web"\n')
    _write(_kit(source), "# Web\n\nUse vite 7.\n")

    assert main(["sync", "--global"]) == 3

    assert "Use vite 7." in _instructions(working)
    assert f"project {broken}: ERROR:" in capsys.readouterr().err


def test_a_project_sync_pushes_its_edits_and_updates_the_other_projects(
    tmp_path: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    vendored = _project(tmp_path, "vendored", "vendored")
    linked = _project(tmp_path, "linked", "linked")
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    asked = _terminal(monkeypatch, "y")
    capsys.readouterr()

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == ["Push them to source ac and update 2 other project(s)? [y/N] "]
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert kit_divergence(editing) == []
    assert _copy(vendored).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert "Use vite 7." in _instructions(linked)
    assert "Use vite 7." in _instructions(editing)
    assert "pushed web to source ac (~/ac/loadout/kits/web), 1 file(s); commit them there" in (
        capsys.readouterr().out
    )


def test_declining_the_push_changes_nothing_outside_the_project(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    linked = _project(tmp_path, "linked", "linked")
    _write(_copy(editing), "# Web\n\nUse webpack.\n")
    asked = _terminal(monkeypatch, "n")

    assert main(["sync", "--root", str(editing)]) == 0

    assert len(asked) == 1
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"
    assert "Use vite." in _instructions(linked)
    assert "Use webpack." in _instructions(editing)
    assert kit_divergence(editing) == ["web"]


def test_without_a_terminal_a_project_sync_never_pushes(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    _write(_copy(editing), "# Web\n\nUse webpack.\n")
    _terminal(monkeypatch, None)

    assert main(["sync", "--root", str(editing)]) == 0

    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"
    assert "Use webpack." in _instructions(editing)


def test_the_sync_a_git_hook_runs_never_pushes(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    _write(_copy(editing), "# Web\n\nUse webpack.\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["hook-event", "post-merge", "--root", str(editing)]) == 0

    assert asked == []
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"
    assert "Use webpack." in _instructions(editing)


def test_a_push_is_held_when_the_source_moved_too(
    tmp_path: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    other = _project(tmp_path, "other", "vendored")
    _write(_copy(editing), "# Web\n\nUse webpack.\n")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == []
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert _copy(other).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"
    err = capsys.readouterr().err
    assert "kit web: edits not pushed — its source changed too" in err
    assert "-Use webpack." in err


def test_a_global_check_previews_the_projects(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vendored = _project(tmp_path, "vendored", "vendored")
    linked = _project(tmp_path, "linked", "linked")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    capsys.readouterr()

    assert main(["check", "--global"]) == 1

    out, err = capsys.readouterr()
    assert f"project {linked}:" in out
    assert f"project {vendored}:" in out
    assert (
        f"note: project {vendored}: kit web: its source changed; `loadout sync --global` updates it"
    ) in out
    assert "DRIFT (source-changed): CLAUDE.md" in err
    assert _copy(vendored).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"


def test_a_global_check_as_json_carries_the_projects(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vendored = _project(tmp_path, "vendored", "vendored")
    linked = _project(tmp_path, "linked", "linked")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    capsys.readouterr()

    assert main(["check", "--global", "--json"]) == 1

    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == 1
    assert (
        f"project {vendored}: kit web: its source changed; `loadout sync --global` updates it"
        in (result["notes"])
    )
    assert any(item["path"] == str(linked / "CLAUDE.md") for item in result["drift"])


def _catalog(source: Path) -> Path:
    kits = source / "loadout" / "kits"
    _write(kits / "instructions" / "mac.md", "# Mac\n\nUse xcodebuild.\n")
    _write(kits / "mac.toml", 'instructions = ["mac"]\n')
    return kits / "instructions" / "mac.md"


def _catalog_project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    _write(root / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["kit", "add", "mac", "--vendored", "--root", str(root)]) == 0
    assert main(["sync", "--root", str(root)]) == 0
    return root


def test_a_global_sync_updates_a_vendored_catalog_kit(tmp_path: Path, source: Path) -> None:
    part = _catalog(source)
    project = _catalog_project(tmp_path, "mac")
    _write(part, "# Mac\n\nUse xcodebuild 17.\n")

    assert main(["sync", "--global"]) == 0

    copy = project / "loadout" / "kits" / "instructions" / "mac.md"
    assert copy.read_text(encoding="utf-8") == "# Mac\n\nUse xcodebuild 17.\n"
    assert "Use xcodebuild 17." in _instructions(project)
    assert kit_divergence(project) == []


def test_a_catalog_edit_is_pushed_and_reaches_the_other_projects(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    part = _catalog(source)
    editing = _catalog_project(tmp_path, "editing")
    other = _catalog_project(tmp_path, "other")
    _write(editing / "loadout" / "kits" / "instructions" / "mac.md", "# Mac\n\nUse tuist.\n")
    _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert part.read_text(encoding="utf-8") == "# Mac\n\nUse tuist.\n"
    assert "Use tuist." in _instructions(other)
    assert kit_divergence(editing) == kit_divergence(other) == []


def test_a_copy_that_already_matches_its_moved_source_is_recorded_without_asking(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    asked = _terminal(monkeypatch, "y")
    assert kit_divergence(editing) == ["web"]

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == []
    assert kit_divergence(editing) == []


def test_a_pushed_shared_part_reaches_the_projects_using_the_other_kit(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _catalog(source)
    kits = source / "loadout" / "kits"
    _write(kits / "skills" / "build" / "SKILL.md", "build\n")
    _write(kits / "mac.toml", 'instructions = ["mac"]\nskills = ["build"]\n')
    _write(kits / "ios.toml", 'skills = ["build"]\n')
    editing = _catalog_project(tmp_path, "editing")
    ios = tmp_path / "ios"
    _write(ios / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["kit", "add", "ios", "--vendored", "--root", str(ios)]) == 0
    assert main(["sync", "--root", str(ios)]) == 0
    _write(editing / "loadout" / "kits" / "skills" / "build" / "SKILL.md", "build, quietly\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == ["Push them to source ac and update 1 other project(s)? [y/N] "]
    copy = ios / "loadout" / "kits" / "skills" / "build" / "SKILL.md"
    assert copy.read_text(encoding="utf-8") == "build, quietly\n"
    assert kit_divergence(ios) == []


def _headers(out: str) -> list[str]:
    return [line for line in out.splitlines() if line.startswith("project ")]


def test_a_global_sync_reaches_only_projects_that_list_a_kit(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    linked = _project(tmp_path, "linked", "linked")
    plain = tmp_path / "plain"
    _write(plain / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["sync", "--root", str(plain)]) == 0
    capsys.readouterr()

    assert main(["sync", "--global"]) == 0

    assert _headers(capsys.readouterr().out) == [f"project {linked}:"]


def test_a_global_sync_in_a_terminal_never_pushes_another_projects_edits(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edited = _project(tmp_path, "edited", "vendored")
    _write(_copy(edited), "# Web\n\nUse webpack.\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--global"]) == 0

    assert asked == []
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite.\n"


def test_a_global_sync_names_copies_it_cannot_update(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    unproven = _project(tmp_path, "unproven", "vendored")
    _write(_copy(unproven), "# Web\n\nUse webpack.\n")
    config = project_config_path(unproven)
    config.write_text(config.read_text(encoding="utf-8").split("\n[kit.web]")[0] + "\n")
    _write(source / "loadout" / "kits" / "api" / "instructions.md", "# API\n")
    orphan = tmp_path / "orphan"
    _write(orphan / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["kit", "add", "api", "--vendored", "--root", str(orphan)]) == 0
    assert main(["sync", "--root", str(orphan)]) == 0
    rmtree(source / "loadout" / "kits" / "api")
    capsys.readouterr()

    assert main(["sync", "--global"]) == 0

    out = capsys.readouterr().out
    assert (
        f"note: project {unproven}: kit web: the vendored copy has no recorded provenance, "
        f"so it is not updated"
    ) in out
    assert f"note: project {orphan}: kit api: not updated: its source does not resolve here" in (
        out
    )


def test_a_global_sync_refuses_a_project_whose_kits_folder_leads_out(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    linked = _project(tmp_path, "linked", "linked")
    escaping = _project(tmp_path, "escaping", "vendored")
    outside = tmp_path / "outside"
    (escaping / "loadout" / "kits").rename(outside)
    (escaping / "loadout" / "kits").symlink_to(outside)
    _write(_kit(source), "# Web\n\nUse vite 7.\n")

    assert main(["sync", "--global"]) == 3

    assert (outside / "web" / "instructions.md").read_text(encoding="utf-8") == (
        "# Web\n\nUse vite.\n"
    )
    assert "Use vite 7." in _instructions(linked)
    assert f"project {escaping}: ERROR:" in capsys.readouterr().err


def test_a_global_check_reports_a_failing_project_and_checks_the_rest(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _project(tmp_path, "broken", "linked")
    working = _project(tmp_path, "working", "vendored")
    _write(broken / "loadout" / "config.toml", 'harnesses = ["claude"]\nkits = "web"\n')
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    capsys.readouterr()

    assert main(["check", "--global", "--json"]) == 3

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error"
    assert [error["target"] for error in result["errors"]] == [str(broken)]
    assert (
        f"project {working}: kit web: its source changed; `loadout sync --global` updates it"
    ) in result["notes"]


def test_two_edited_kits_sharing_a_part_are_both_pushed(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kits = source / "loadout" / "kits"
    _write(kits / "instructions" / "mac.md", "# Mac\n")
    _write(kits / "instructions" / "ios.md", "# iOS\n")
    _write(kits / "skills" / "build" / "SKILL.md", "build\n")
    _write(kits / "mac.toml", 'instructions = ["mac"]\nskills = ["build"]\n')
    _write(kits / "ios.toml", 'instructions = ["ios"]\nskills = ["build"]\n')
    editing = tmp_path / "editing"
    _write(editing / "loadout" / "config.toml", 'harnesses = ["claude"]\n')
    assert main(["kit", "add", "mac", "--vendored", "--root", str(editing)]) == 0
    assert main(["kit", "add", "ios", "--vendored", "--root", str(editing), "--yes"]) == 0
    _write(editing / "loadout" / "kits" / "skills" / "build" / "SKILL.md", "build, quietly\n")
    _write(editing / "loadout" / "kits" / "instructions" / "ios.md", "# iOS, edited\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert len(asked) == 2
    assert (kits / "skills" / "build" / "SKILL.md").read_text(encoding="utf-8") == (
        "build, quietly\n"
    )
    assert (kits / "instructions" / "ios.md").read_text(encoding="utf-8") == "# iOS, edited\n"
    assert kit_divergence(editing) == []


def test_a_broken_project_elsewhere_does_not_stop_a_push(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    other = _project(tmp_path, "other", "vendored")
    broken = _project(tmp_path, "broken", "linked")
    _write(broken / "loadout" / "config.toml", 'harnesses = ["claude"]\nkits = "web"\n')
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == ["Push them to source ac and update 1 other project(s)? [y/N] "]
    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert _copy(other).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"


def test_a_project_that_fails_after_a_push_sets_the_exit_code(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    linked = _project(tmp_path, "linked", "linked")
    other = _project(tmp_path, "other", "vendored")
    _write(linked / "CLAUDE.md", "edited by hand\n")
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 1

    assert _kit(source).read_text(encoding="utf-8") == "# Web\n\nUse vite 7.\n"
    assert "Use vite 7." in _instructions(other)
    assert (linked / "CLAUDE.md").read_text(encoding="utf-8") == "edited by hand\n"


def test_the_push_prompt_shows_the_change_and_escapes_file_names(
    tmp_path: Path,
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    editing = _project(tmp_path, "editing", "vendored")
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    _write(_copy(editing).parent / "q\x1b[2Kz.md", "hidden\n")
    _terminal(monkeypatch, "n")
    capsys.readouterr()

    assert main(["sync", "--root", str(editing)]) == 0

    out, err = capsys.readouterr()
    assert "+Use vite 7." in err
    assert "q\\x1b[2Kz.md" in out
    assert "\x1b" not in out


def test_a_copy_without_provenance_is_not_offered_beside_one_that_is(
    tmp_path: Path, source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(source / "loadout" / "kits" / "api" / "instructions.md", "# API\n")
    editing = _project(tmp_path, "editing", "vendored")
    assert main(["kit", "add", "api", "--vendored", "--root", str(editing)]) == 0
    _write(_copy(editing), "# Web\n\nUse vite 7.\n")
    _write(vendored_path(editing, "api") / "instructions.md", "# API, edited\n")
    config = project_config_path(editing)
    config.write_text(config.read_text(encoding="utf-8").split("\n[kit.api]")[0] + "\n")
    asked = _terminal(monkeypatch, "y")

    assert main(["sync", "--root", str(editing)]) == 0

    assert asked == ["Push them to source ac? [y/N] "]
    assert (source / "loadout" / "kits" / "api" / "instructions.md").read_text(
        encoding="utf-8"
    ) == "# API\n"


def test_a_global_sync_records_a_copy_that_already_matches_its_source(
    tmp_path: Path, source: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    level = _project(tmp_path, "level", "vendored")
    _write(_copy(level), "# Web\n\nUse vite 7.\n")
    _write(_kit(source), "# Web\n\nUse vite 7.\n")
    assert kit_divergence(level) == ["web"]
    capsys.readouterr()

    assert main(["sync", "--global"]) == 0

    assert kit_divergence(level) == []
    assert f"note: project {level}: kit web: it matches its source; recorded its hash" in (
        capsys.readouterr().out
    )
