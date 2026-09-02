from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import write_all
from loadout.errors import LoadoutError
from loadout.skill_installation import (
    OWNER_MARKER,
    SourceSkillState,
    inspect_skill_source,
    install_skill_source,
    uninstall_skill_source,
)
from loadout.skills import discover_skills


def _bundle(path: Path, body: str = "v1\n") -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: loadout\ndescription: Use when configuring loadout.\n---\n\n" + body,
        encoding="utf-8",
    )
    reference = path / "references"
    reference.mkdir()
    (reference / "configuration.md").write_text("configuration\n", encoding="utf-8")
    return path


def _root(tmp_path: Path, sources: tuple[str, ...] = ("primary",)) -> Path:
    root = tmp_path / "global"
    root.mkdir()
    blocks: list[str] = []
    for name in sources:
        source = root / name
        source.mkdir()
        (source / "permissions.toml").write_text("[shell]\n", encoding="utf-8")
        blocks.append(f'[[source]]\nname = "{name}"\npath = "{name}"')
    (root / "loadout.toml").write_text(
        "\n\n".join([*blocks, "[claude]", "[codex]"]) + "\n",
        encoding="utf-8",
    )
    return root


def test_one_skills_source_is_selected_by_default(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    location = inspect_skill_source(root, "default", bundle)
    assert location.source == "primary"
    assert location.path == root / "primary" / "skills" / "loadout"
    assert location.state is SourceSkillState.MISSING


def test_multiple_skills_sources_require_an_explicit_source(tmp_path: Path) -> None:
    root = _root(tmp_path, ("one", "two"))
    bundle = _bundle(tmp_path / "bundle")
    with pytest.raises(LoadoutError, match="--source"):
        inspect_skill_source(root, "default", bundle)
    assert inspect_skill_source(root, "default", bundle, source_name="two").source == "two"


def test_profile_selects_its_own_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    second = root / "secondary"
    second.mkdir()
    (second / "permissions.toml").write_text("[shell]\n", encoding="utf-8")
    (root / "personal.toml").write_text(
        'extends = "default"\n\n[[source]]\nname = "secondary"\npath = "secondary"\n',
        encoding="utf-8",
    )
    bundle = _bundle(tmp_path / "bundle")
    location = inspect_skill_source(root, "personal", bundle)
    assert location.source == "secondary"
    assert location.path == second / "skills" / "loadout"


def test_unknown_profile_is_refused_before_selecting_a_source(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    with pytest.raises(LoadoutError, match="unknown profile 'missing'"):
        inspect_skill_source(root, "missing", bundle)
    assert not (root / "primary" / "skills" / "loadout").exists()


def test_an_existing_unowned_skill_is_a_conflict(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    existing = root / "primary" / "skills" / "loadout"
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("mine\n", encoding="utf-8")
    location = inspect_skill_source(root, "default", bundle)
    assert location.state is SourceSkillState.CONFLICTING
    with pytest.raises(LoadoutError, match="not owned"):
        install_skill_source(location, bundle)
    assert (existing / "SKILL.md").read_text(encoding="utf-8") == "mine\n"


def test_install_vendors_the_bundle_as_a_normal_source_skill(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    location = inspect_skill_source(root, "default", bundle)
    assert install_skill_source(location, bundle)
    installed = inspect_skill_source(root, "default", bundle)
    assert installed.state is SourceSkillState.INSTALLED
    assert (installed.path / "SKILL.md").read_bytes() == (bundle / "SKILL.md").read_bytes()
    assert (installed.path / OWNER_MARKER).is_file()
    (skill,) = discover_skills(installed.path.parent)
    assert OWNER_MARKER not in {str(path) for path in skill.supporting}


def test_install_refreshes_an_unmodified_older_bundle(tmp_path: Path) -> None:
    root = _root(tmp_path)
    first = _bundle(tmp_path / "first", "v1\n")
    install_skill_source(inspect_skill_source(root, "default", first), first)
    second = _bundle(tmp_path / "second", "v2\n")
    stale = inspect_skill_source(root, "default", second)
    assert stale.state is SourceSkillState.UPDATE_AVAILABLE
    assert install_skill_source(stale, second)
    installed = inspect_skill_source(root, "default", second)
    assert installed.state is SourceSkillState.INSTALLED
    assert "v2" in (installed.path / "SKILL.md").read_text(encoding="utf-8")


def test_install_preserves_a_modified_installed_copy(tmp_path: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    install_skill_source(inspect_skill_source(root, "default", bundle), bundle)
    installed_path = root / "primary" / "skills" / "loadout" / "SKILL.md"
    installed_path.write_text("my edit\n", encoding="utf-8")
    modified = inspect_skill_source(root, "default", bundle)
    assert modified.state is SourceSkillState.MODIFIED
    with pytest.raises(LoadoutError, match="modified"):
        install_skill_source(modified, bundle)
    assert installed_path.read_text(encoding="utf-8") == "my edit\n"


def test_normal_sync_deploys_the_installed_source_to_configured_agents(
    tmp_path: Path, fake_home: Path
) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    install_skill_source(inspect_skill_source(root, "default", bundle), bundle)
    write_all(root)
    assert (fake_home / ".claude" / "skills" / "loadout" / "SKILL.md").is_file()
    assert (fake_home / ".codex" / "skills" / "loadout" / "SKILL.md").is_file()
    assert not (fake_home / ".claude" / "skills" / "loadout" / OWNER_MARKER).exists()


def test_uninstall_removes_owned_source_and_generated_outputs(
    tmp_path: Path, fake_home: Path
) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    install_skill_source(inspect_skill_source(root, "default", bundle), bundle)
    write_all(root)
    location = inspect_skill_source(root, "default", bundle)
    removed = uninstall_skill_source(location, root=root, profile="default")
    assert removed
    assert not location.path.exists()
    assert not (fake_home / ".claude" / "skills" / "loadout" / "SKILL.md").exists()
    assert not (fake_home / ".codex" / "skills" / "loadout" / "SKILL.md").exists()


def test_uninstall_refuses_a_modified_generated_output(tmp_path: Path, fake_home: Path) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    install_skill_source(inspect_skill_source(root, "default", bundle), bundle)
    write_all(root)
    output = fake_home / ".claude" / "skills" / "loadout" / "SKILL.md"
    output.write_text("my output edit\n", encoding="utf-8")
    location = inspect_skill_source(root, "default", bundle)
    with pytest.raises(LoadoutError, match="modified outside loadout"):
        uninstall_skill_source(location, root=root, profile="default")
    assert location.path.is_dir()
    assert output.read_text(encoding="utf-8") == "my output edit\n"


def test_uninstall_preserves_unrelated_files_in_the_generated_skill_directory(
    tmp_path: Path, fake_home: Path
) -> None:
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    install_skill_source(inspect_skill_source(root, "default", bundle), bundle)
    write_all(root)
    extra = fake_home / ".claude" / "skills" / "loadout" / "notes.txt"
    extra.write_text("keep me\n", encoding="utf-8")
    location = inspect_skill_source(root, "default", bundle)
    uninstall_skill_source(location, root=root, profile="default")
    assert extra.read_text(encoding="utf-8") == "keep me\n"
