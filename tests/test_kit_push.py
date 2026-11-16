from __future__ import annotations

from pathlib import Path

import pytest

from loadout import kit_vendor
from loadout.bundled_kits import bundled_kit
from loadout.errors import LoadoutError
from loadout.kit_push import Held, Level, Push, apply_push, plan_push
from loadout.kits import (
    copy_tree,
    declare,
    kit_divergence,
    record_hash,
    tree_hash,
    vendored_path,
)
from loadout.project import load_project_config, project_config_path


def _machine(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """This machine's global source, offering kits from `ac/loadout/kits/`."""
    source = fake_home / "ac"
    (source / "loadout" / "kits").mkdir(parents=True, exist_ok=True)
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
        encoding="utf-8",
    )
    xdg = fake_home / ".config"
    (xdg / "loadout").mkdir(parents=True, exist_ok=True)
    (xdg / "loadout" / "config.toml").write_text(f'source = "{source}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return source / "loadout" / "kits"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _vendor(project: Path, name: str, upstream: Path) -> Path:
    local = vendored_path(project, name)
    if upstream.is_file():
        local = local.with_name(f"{local.name}.toml")
    copy_tree(upstream, local)
    declare(project, name)
    record_hash(project, name, tree_hash(local))
    return local


def _recorded(project: Path, name: str) -> str | None:
    return load_project_config(project_config_path(project)).vendored_hash(name)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def kits(fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _machine(fake_home, monkeypatch)


@pytest.fixture
def web(kits: Path) -> Path:
    _write(kits / "web" / "instructions.md", "# Web\n")
    _write(kits / "web" / "permissions.toml", '[shell]\nallow = ["vite"]\n')
    return kits / "web"


@pytest.fixture
def catalog(kits: Path) -> Path:
    _write(kits / "instructions" / "mac.md", "# Mac\n")
    _write(kits / "instructions" / "ios.md", "# iOS\n")
    _write(kits / "skills" / "build" / "SKILL.md", "build\n")
    _write(kits / "skills" / "build" / "notes.md", "notes\n")
    _write(kits / "mac.toml", 'instructions = ["mac"]\nskills = ["build"]\n')
    _write(kits / "ios.toml", 'instructions = ["ios"]\nskills = ["build"]\n')
    return kits


def test_an_edited_directory_copy_pushes_its_edits_and_deletions(
    bare_project: Path, web: Path
) -> None:
    local = _vendor(bare_project, "web", web)
    _write(local / "instructions.md", "# Web, edited\n")
    (local / "permissions.toml").unlink()

    push = plan_push(bare_project, "web")

    assert isinstance(push, Push)
    assert {(path, content is None) for path, content in push.changes} == {
        (web / "instructions.md", False),
        (web / "permissions.toml", True),
    }
    apply_push(bare_project, push)
    assert _text(web / "instructions.md") == "# Web, edited\n"
    assert not (web / "permissions.toml").exists()
    assert _recorded(bare_project, "web") == tree_hash(local) == tree_hash(web)
    assert kit_divergence(bare_project) == []


def test_a_push_is_held_when_the_source_moved_too(bare_project: Path, web: Path) -> None:
    local = _vendor(bare_project, "web", web)
    _write(local / "instructions.md", "# Web, ours\n")
    _write(web / "instructions.md", "# Web, theirs\n")

    held = plan_push(bare_project, "web")

    assert held == Held("web", "its source changed too since the copy was vendored", web)
    assert _text(web / "instructions.md") == "# Web, theirs\n"


def test_a_copy_that_does_not_load_is_held_before_anything_is_written(
    bare_project: Path, web: Path
) -> None:
    local = _vendor(bare_project, "web", web)
    _write(local / "permissions.toml", '[shell]\nallow = ["*"]\n')

    held = plan_push(bare_project, "web")

    assert isinstance(held, Held)
    assert "*" in held.reason
    assert held.upstream is None
    assert _text(web / "permissions.toml") == '[shell]\nallow = ["vite"]\n'


def test_a_kit_vendored_from_the_bundled_copy_is_held(bare_project: Path) -> None:
    upstream = bundled_kit("frontend")
    assert upstream is not None
    local = _vendor(bare_project, "frontend", upstream)
    _write(local / "instructions.md", "# Frontend, edited\n")

    held = plan_push(bare_project, "frontend")

    assert isinstance(held, Held)
    assert "bundled" in held.reason
    assert held.upstream is None


def test_a_bundled_copy_equal_to_its_source_is_level_not_held(bare_project: Path) -> None:
    upstream = bundled_kit("frontend")
    assert upstream is not None
    _vendor(bare_project, "frontend", upstream)
    record_hash(bare_project, "frontend", "sha256:stale")

    assert plan_push(bare_project, "frontend") == Level("frontend")


def test_a_catalog_push_names_and_records_the_other_kit_sharing_a_part(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    ios = _vendor(bare_project, "ios", catalog / "ios.toml")
    _write(mac.parent / "skills" / "build" / "SKILL.md", "build, edited\n")

    push = plan_push(bare_project, "mac")

    assert isinstance(push, Push)
    assert push.also == ("ios",)
    assert [path for path, _ in push.changes] == [catalog / "skills" / "build" / "SKILL.md"]
    apply_push(bare_project, push)
    assert _recorded(bare_project, "ios") == tree_hash(ios) == tree_hash(catalog / "ios.toml")
    assert kit_divergence(bare_project) == []


def test_a_new_file_in_a_shared_part_still_names_the_other_kit(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _write(mac.parent / "skills" / "build" / "extra.md", "extra\n")

    push = plan_push(bare_project, "mac")

    assert isinstance(push, Push)
    assert push.also == ("ios",)
    assert [path for path, _ in push.changes] == [catalog / "skills" / "build" / "extra.md"]


def test_a_file_removed_from_a_skill_part_is_removed_from_the_source(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    (mac.parent / "skills" / "build" / "notes.md").unlink()

    push = plan_push(bare_project, "mac")

    assert isinstance(push, Push)
    assert push.changes == ((catalog / "skills" / "build" / "notes.md", None),)


def test_a_newly_listed_part_never_loses_files_the_source_had(
    bare_project: Path, catalog: Path
) -> None:
    _write(catalog / "skills" / "deploy" / "SKILL.md", "deploy\n")
    _write(catalog / "skills" / "deploy" / "extra.md", "extra\n")
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _write(mac.parent / "skills" / "deploy" / "SKILL.md", "deploy\n")
    _write(mac, 'instructions = ["mac"]\nskills = ["build", "deploy"]\n')

    push = plan_push(bare_project, "mac")

    assert isinstance(push, Push)
    assert [path for path, _ in push.changes] == [catalog / "mac.toml"]
    apply_push(bare_project, push)
    assert _text(catalog / "skills" / "deploy" / "extra.md") == "extra\n"


def test_a_part_the_manifest_drops_stays_in_the_source(bare_project: Path, catalog: Path) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _write(mac, 'instructions = ["mac"]\n')

    push = plan_push(bare_project, "mac")

    assert isinstance(push, Push)
    assert [path for path, _ in push.changes] == [catalog / "mac.toml"]
    apply_push(bare_project, push)
    assert _text(catalog / "skills" / "build" / "SKILL.md") == "build\n"


def test_a_new_part_that_would_replace_a_different_source_file_is_held(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _write(catalog / "instructions" / "shared.md", "# Shared, theirs\n")
    _write(mac.parent / "instructions" / "shared.md", "# Shared, ours\n")
    _write(mac, 'instructions = ["mac", "shared"]\nskills = ["build"]\n')

    held = plan_push(bare_project, "mac")

    assert isinstance(held, Held)
    assert str(catalog / "instructions" / "shared.md") in held.reason
    assert _text(catalog / "instructions" / "shared.md") == "# Shared, theirs\n"


def test_a_source_catalog_that_no_longer_loads_is_held_not_raised(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _write(mac.parent / "instructions" / "mac.md", "# Mac, edited\n")
    _write(catalog / "mac.toml", 'instructions = ["mac", "missing"]\nskills = ["build"]\n')

    held = plan_push(bare_project, "mac")

    assert isinstance(held, Held)
    assert "missing" in held.reason


def test_a_kit_sharing_a_part_this_run_pushed_is_planned_from_the_runs_start(
    bare_project: Path, catalog: Path
) -> None:
    mac = _vendor(bare_project, "mac", catalog / "mac.toml")
    _vendor(bare_project, "ios", catalog / "ios.toml")
    start = tree_hash(catalog / "ios.toml")
    _write(mac.parent / "skills" / "build" / "SKILL.md", "build, edited\n")
    _write(mac.parent / "instructions" / "ios.md", "# iOS, edited\n")
    first = plan_push(bare_project, "mac")
    assert isinstance(first, Push)
    apply_push(bare_project, first)

    moved = plan_push(bare_project, "ios")
    second = plan_push(bare_project, "ios", start=start)

    assert isinstance(moved, Held)
    assert isinstance(second, Push)
    assert [path for path, _ in second.changes] == [catalog / "instructions" / "ios.md"]
    apply_push(bare_project, second)
    assert kit_divergence(bare_project) == []


def test_a_source_changed_after_planning_refuses_the_push(bare_project: Path, web: Path) -> None:
    local = _vendor(bare_project, "web", web)
    _write(local / "instructions.md", "# Web, ours\n")
    push = plan_push(bare_project, "web")
    assert isinstance(push, Push)
    _write(web / "instructions.md", "# Web, edited meanwhile\n")

    with pytest.raises(LoadoutError, match="changed while waiting"):
        apply_push(bare_project, push)

    assert _text(web / "instructions.md") == "# Web, edited meanwhile\n"
    assert kit_divergence(bare_project) == ["web"]


def test_a_write_that_fails_restores_the_source(
    bare_project: Path, web: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = _vendor(bare_project, "web", web)
    _write(local / "instructions.md", "# Web, ours\n")
    _write(local / "permissions.toml", '[shell]\nallow = ["vite", "tsc"]\n')
    push = plan_push(bare_project, "web")
    assert isinstance(push, Push)
    assert len(push.changes) == 2
    install = kit_vendor.atomic_install
    calls: list[Path] = []

    def failing(path: Path, content: object) -> None:
        calls.append(path)
        if len(calls) == 2:
            raise OSError("disk full")
        install(path, content)  # type: ignore[arg-type]

    monkeypatch.setattr(kit_vendor, "atomic_install", failing)

    with pytest.raises(LoadoutError, match="push of web failed; the source is as it was"):
        apply_push(bare_project, push)

    assert _text(web / "instructions.md") == "# Web\n"
    assert _text(web / "permissions.toml") == '[shell]\nallow = ["vite"]\n'
