"""The project write rule, the input rule and the profile rule, called directly so the
unit tier holds them (review 2, batch 5 items 2, 4 and 9)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from loadout.emit import RenderContext, refuse_unregistered_global, render_all
from loadout.errors import LoadoutError
from loadout.machine import resolve_profile
from loadout.notices import collect_notices
from loadout.project import refuse_escaping, refuse_external_links
from registration import register


def _project(root: Path) -> Path:
    (root / "loadout/skills/review").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/skills/review/SKILL.md").write_text("---\nname: review\n---\nR.\n")
    return root


def test_a_link_cycle_inside_the_project_is_walked_once(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / "loadout/skills/loop").symlink_to("..", target_is_directory=True)
    failures: list[LoadoutError] = []

    def walk() -> None:
        try:
            refuse_external_links(root)
        except LoadoutError as error:
            failures.append(error)

    walker = threading.Thread(target=walk, daemon=True)
    walker.start()
    walker.join(timeout=10)

    assert walker.is_alive() is False
    assert failures == []


def test_a_link_out_of_the_project_is_refused_by_name(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / "loadout/skills/review/notes").symlink_to(tmp_path)

    with pytest.raises(LoadoutError, match=r"skills/review/notes -> "):
        refuse_external_links(root)


def test_a_link_into_the_git_dir_is_refused(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / ".git").mkdir()
    (root / "loadout/skills/review/hooks").symlink_to(root / ".git")

    with pytest.raises(LoadoutError, match=r"into \.git"):
        refuse_external_links(root)


@pytest.mark.parametrize(
    ("relative", "reason"),
    [
        (".git/hooks/pre-commit", r"under \.git/"),
        (".GIT/config", r"under \.GIT/"),
        ("loadout/permissions.toml", "under loadout/"),
    ],
)
def test_a_protected_folder_is_never_written(tmp_path: Path, relative: str, reason: str) -> None:
    root = tmp_path / "p"
    root.mkdir()

    with pytest.raises(LoadoutError, match=reason):
        refuse_escaping(root, [root / relative])


def test_a_path_outside_the_project_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()

    with pytest.raises(LoadoutError, match="outside the project"):
        refuse_escaping(root, [tmp_path / "elsewhere.md"])


def test_a_link_between_the_root_and_a_path_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / ".claude").symlink_to(tmp_path)

    with pytest.raises(LoadoutError, match="through the link"):
        refuse_escaping(root, [root / ".claude/settings.json"])


def test_a_leaf_link_to_another_output_is_written_through(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "AGENTS.md").write_text("rules\n")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")

    refuse_escaping(root, [root / "CLAUDE.md", root / "AGENTS.md"], [root / "AGENTS.md"])

    assert (root / "CLAUDE.md").readlink() == Path("AGENTS.md")


def test_a_leaf_link_to_a_file_the_render_does_not_write_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "notes.md").write_text("mine\n")
    (root / "CLAUDE.md").symlink_to("notes.md")

    with pytest.raises(LoadoutError, match="a link to a file this sync does not write"):
        refuse_escaping(root, [root / "CLAUDE.md"], [root / "CLAUDE.md"])


def _register(home: Path, source: Path, extra: str = "") -> None:
    machine = home / ".config/loadout"
    machine.mkdir(parents=True, exist_ok=True)
    (machine / "config.toml").write_text(f'source = "{source}"\nprofile = "work"\n{extra}')


def test_an_explicit_profile_wins_over_the_machine(tmp_path: Path, fake_home: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _register(fake_home, source)

    assert resolve_profile(source) == "work"
    assert resolve_profile(source, "home") == "home"


def test_the_registered_source_is_recognised_through_a_link(
    tmp_path: Path, fake_home: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (tmp_path / "alias").symlink_to(source, target_is_directory=True)
    _register(fake_home, source)

    assert resolve_profile(tmp_path / "alias") == "work"


def test_the_registered_source_is_recognised_by_a_relative_root(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _register(fake_home, source)
    monkeypatch.chdir(tmp_path)

    assert resolve_profile(Path("source")) == "work"


def test_an_unreadable_machine_config_stops_only_a_global_source(
    tmp_path: Path, fake_home: Path
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "loadout.toml").write_text("")
    project = tmp_path / "project"
    project.mkdir()
    _register(fake_home, source, 'profle = "typo"\n')

    with pytest.raises(LoadoutError, match="profle"):
        resolve_profile(source)
    assert resolve_profile(source, lenient=True) == "default"
    assert resolve_profile(project) == "default"


def _global_source(root: Path) -> Path:
    (root / "instructions").mkdir(parents=True)
    (root / "instructions/x.md").write_text("Rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/mine.md"]\norder = ["x"]\n'
    )
    return root


def test_an_unregistered_global_source_is_refused_by_name(tmp_path: Path) -> None:
    source = _global_source(tmp_path / "source")
    register(tmp_path / "elsewhere")

    with pytest.raises(LoadoutError, match=rf"^{source}: this folder holds a global setup"):
        render_all(source)


def test_the_registered_source_renders_through_a_link(tmp_path: Path, fake_home: Path) -> None:
    source = register(_global_source(tmp_path / "source"))
    (tmp_path / "alias").symlink_to(source, target_is_directory=True)

    assert list(render_all(tmp_path / "alias")) == [fake_home / "mine.md"]


def test_a_render_context_names_the_root_it_may_render(tmp_path: Path, fake_home: Path) -> None:
    source = _global_source(tmp_path / "source")

    outputs = render_all(source, context=RenderContext(registered=lambda: source))

    assert list(outputs) == [fake_home / "mine.md"]


def test_a_manifest_not_on_disk_is_refused_when_held(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="this folder holds a global setup"):
        refuse_unregistered_global(tmp_path, held=True)


def test_notices_refuse_an_unregistered_global_source(tmp_path: Path) -> None:
    source = _global_source(tmp_path / "source")

    with pytest.raises(LoadoutError, match="this folder holds a global setup"):
        collect_notices(source)
