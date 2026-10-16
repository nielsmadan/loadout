"""The project write rule, the input rule and the profile rule, called directly so the
unit tier holds them (review 2, batch 5 items 2, 4 and 9)."""

from __future__ import annotations

import threading
from pathlib import Path, PurePosixPath

import pytest

from loadout import sync
from loadout.containment import (
    first_link,
    protected,
    refuse_escaping,
    refuse_external_links,
    within,
)
from loadout.emit import RenderContext, refuse_unregistered_global, render_all
from loadout.errors import LoadoutError
from loadout.machine import resolve_profile
from loadout.notices import collect_notices
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


def test_an_input_link_inside_the_project_but_out_of_loadout_is_refused(tmp_path: Path) -> None:
    """Review 3, 1.9: inputs come from `loadout/` alone, so a gitignored `.env` beside it
    is not copied into an output."""
    root = _project(tmp_path / "p")
    (root / ".env").write_text("TOKEN=x\n")
    (root / "loadout/skills/review/env.txt").symlink_to("../../../.env")

    with pytest.raises(LoadoutError, match=r"skills/review/env\.txt -> .*\.env, out of loadout/"):
        refuse_external_links(root)


def test_an_input_link_that_stays_inside_loadout_is_read(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / "loadout/instructions").mkdir()
    (root / "loadout/instructions/x.md").write_text("Rules.\n")
    (root / "loadout/skills/review/rules.md").symlink_to("../../instructions/x.md")

    refuse_external_links(root)

    assert (root / "loadout/skills/review/rules.md").read_text() == "Rules.\n"


def test_a_folder_reached_through_a_link_inside_loadout_is_walked_for_what_it_holds(
    tmp_path: Path,
) -> None:
    """An excluded folder is never entered by its own path, but a link to it from another
    skill is read under that skill's name, so the walk follows the link."""
    root = _project(tmp_path / "p")
    hidden = root / "loadout/skills/review/node_modules"
    hidden.mkdir()
    (hidden / "key").symlink_to(tmp_path / "outside")
    (root / "loadout/skills/other").mkdir()
    (root / "loadout/skills/other/lib").symlink_to(
        "../review/node_modules", target_is_directory=True
    )

    with pytest.raises(
        LoadoutError, match=r"skills/other/lib/key -> .*outside, outside the project"
    ):
        refuse_external_links(root, frozenset({"node_modules"}))


def test_a_source_folder_that_is_itself_a_link_is_refused(tmp_path: Path) -> None:
    """Review 3, 1.8: `loadout -> .claude` let sync write its outputs into the source."""
    root = tmp_path / "p"
    _project(root)
    (root / "loadout").rename(root / ".claude")
    (root / "loadout").symlink_to(".claude", target_is_directory=True)

    with pytest.raises(LoadoutError, match=r"^.*/p/loadout is a link"):
        refuse_external_links(root)


def test_a_link_out_of_the_project_is_refused_by_name(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")
    (root / "loadout/skills/review/notes").symlink_to(tmp_path)

    with pytest.raises(LoadoutError, match=r"skills/review/notes -> "):
        refuse_external_links(root)


@pytest.mark.parametrize("spelling", [".git", ".GIT"])
def test_a_link_into_the_git_dir_is_refused(tmp_path: Path, spelling: str) -> None:
    """Case-folded, as a case-insensitive disk reads `.GIT` as the git dir (review 3, 5.3)."""
    root = _project(tmp_path / "p")
    (root / spelling).mkdir()
    (root / "loadout/skills/review/hooks").symlink_to(root / spelling)

    with pytest.raises(LoadoutError, match=rf"hooks -> .*/p/\{spelling}, into \.git"):
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


@pytest.mark.parametrize(
    ("leaf", "target"),
    [(".claude/settings.json", "../AGENTS.md"), (".mcp.json", ".claude/settings.json")],
)
def test_a_leaf_link_between_two_other_outputs_is_refused(
    tmp_path: Path, leaf: str, target: str
) -> None:
    """Review 3, 1.11: syncing through `.claude/settings.json -> ../AGENTS.md` replaced the
    instructions with settings JSON. Only CLAUDE.md -> AGENTS.md is a documented reading."""
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    (root / "AGENTS.md").write_text("rules\n")
    (root / ".claude/settings.json").write_text("{}\n")
    (root / leaf).unlink(missing_ok=True)
    (root / leaf).symlink_to(target)
    outputs = [root / "AGENTS.md", root / ".claude/settings.json", root / ".mcp.json"]

    with pytest.raises(LoadoutError, match="a link from one output to another"):
        refuse_escaping(root, [root / leaf], outputs)


def test_a_leaf_link_to_a_file_the_render_does_not_write_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / "notes.md").write_text("mine\n")
    (root / "CLAUDE.md").symlink_to("notes.md")

    with pytest.raises(LoadoutError, match="a link sync does not write through"):
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


def test_a_folder_shared_by_many_outputs_is_checked_for_a_link_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 3, batch 3.5: each output asked again of every folder above it."""
    root = tmp_path / "p"
    outputs = [root / f".claude/skills/s{index}/SKILL.md" for index in range(20)]
    checked: list[Path] = []
    is_symlink = Path.is_symlink

    def counting(self: Path) -> bool:
        checked.append(self)
        return is_symlink(self)

    monkeypatch.setattr(Path, "is_symlink", counting)

    refuse_escaping(root, outputs, outputs)

    assert checked.count(root / ".claude") == 1
    assert checked.count(root / ".claude/skills") == 1


def test_within_reads_a_path_through_the_root_or_its_resolved_form(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)

    assert within(alias, alias / "a/b") == PurePosixPath("a/b")
    assert within(alias, real / "a") == PurePosixPath("a")
    assert within(alias, tmp_path / "elsewhere") is None


@pytest.mark.parametrize(
    ("relative", "folder"),
    [
        (".git/hooks", ".git"),
        (".GIT/config", ".GIT"),
        ("loadout/x", "loadout"),
        (".claude/x", None),
    ],
)
def test_protected_names_the_folder_loadout_never_writes(relative: str, folder: str | None) -> None:
    assert protected(PurePosixPath(relative)) == folder


def test_first_link_is_the_shallowest_link_on_the_way_including_the_path(tmp_path: Path) -> None:
    (tmp_path / "real/inner").mkdir(parents=True)
    (tmp_path / "a").mkdir()
    (tmp_path / "a/b").symlink_to(tmp_path / "real", target_is_directory=True)
    (tmp_path / "leaf").symlink_to(tmp_path / "real")

    assert first_link(tmp_path, tmp_path / "a/b/inner") == tmp_path / "a/b"
    assert first_link(tmp_path, tmp_path / "leaf") == tmp_path / "leaf"
    assert first_link(tmp_path, tmp_path / "real/inner") is None


def _aliased(tmp_path: Path) -> tuple[Path, Path]:
    """A project reached through a link above it, as macOS's /var is; a resolved TMPDIR,
    as on Linux CI, never exercises the second spelling (review 3, batch 5.2)."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    return alias, real


def test_an_output_written_through_the_alias_matches_one_named_by_the_resolved_path(
    tmp_path: Path,
) -> None:
    alias, real = _aliased(tmp_path)
    (real / "AGENTS.md").write_text("rules\n")
    (real / "CLAUDE.md").symlink_to("AGENTS.md")

    refuse_escaping(alias, [alias / "CLAUDE.md"], [real / "AGENTS.md"])

    assert (real / "CLAUDE.md").readlink() == Path("AGENTS.md")


def test_a_path_spelled_through_the_alias_is_inside_the_project(tmp_path: Path) -> None:
    """Refused as "outside the project" if only the resolved spelling counted."""
    alias, real = _aliased(tmp_path)

    refuse_escaping(alias, [alias / "AGENTS.md"], [real / "AGENTS.md"])


def test_an_orphan_recorded_under_the_resolved_path_is_the_project_s(tmp_path: Path) -> None:
    alias, real = _aliased(tmp_path)
    outside = tmp_path / "elsewhere/x.md"

    assert sync._in_project(alias, [real / ".claude/x.md", outside]) == [real / ".claude/x.md"]
