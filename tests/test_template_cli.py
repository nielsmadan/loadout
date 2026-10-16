from __future__ import annotations

import sys
from pathlib import Path

import pytest

from loadout.cli import main
from loadout.project import load_project_config, project_config_path
from loadout.templates import tree_hash, vendored_path

pytestmark = pytest.mark.integration


def _upstream(fake_home: Path, monkeypatch: pytest.MonkeyPatch, name: str, text: str) -> Path:
    """A template offered by this machine's global source, resolvable by name."""
    source = fake_home / "ac"
    tree = source / "loadout" / "templates" / name
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "permissions.toml").write_text(text, encoding="utf-8")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
        encoding="utf-8",
    )
    xdg = fake_home / ".config"
    (xdg / "loadout").mkdir(parents=True, exist_ok=True)
    (xdg / "loadout" / "config.toml").write_text(f'source = "{source}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return tree


def _config(project: Path) -> Path:
    return project_config_path(project)


def test_list_reports_a_project_with_no_templates(
    bare_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    assert "no templates" in capsys.readouterr().out


def test_add_tracked_declares_without_copying_anything(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--tracked", "--root", str(bare_project)]) == 0
    assert load_project_config(_config(bare_project)).templates == ("web",)
    assert not (bare_project / "loadout" / "templates").exists()


def test_add_refuses_a_name_that_does_not_resolve(bare_project: Path) -> None:
    assert main(["template", "add", "web", "--tracked", "--root", str(bare_project)]) == 3
    assert load_project_config(_config(bare_project)).templates == ()


def test_add_vendored_copies_the_tree_and_records_the_hash(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--vendored", "--root", str(bare_project)]) == 0

    vendored = bare_project / "loadout" / "templates" / "web"
    assert (vendored / "permissions.toml").read_text(encoding="utf-8") == (
        '[shell]\nallow = ["vite"]\n'
    )
    config = load_project_config(_config(bare_project))
    assert config.templates == ("web",)
    assert config.vendored_hash("web") == tree_hash(vendored)


def test_add_vendored_refuses_when_a_copy_is_already_there(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    assert main(["template", "add", "web", "--vendored", "--root", str(bare_project)]) == 3


def test_vendor_is_not_a_command(bare_project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["template", "vendor", "web", "--root", str(bare_project)]) == 2
    assert "invalid choice: 'vendor'" in capsys.readouterr().err


def test_bare_add_refuses_without_a_tty(
    bare_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["template", "add", "--root", str(bare_project)]) == 2
    assert "needs a name" in capsys.readouterr().err


def test_named_add_without_mode_refuses_without_a_tty(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--root", str(bare_project)]) == 2
    assert load_project_config(_config(bare_project)).templates == ()


def test_bare_add_lists_available_templates_and_takes_a_tracked_choice(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The case `template list` cannot surface: a resolvable template that is
    not declared yet."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(("1", "tracked"))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main(["template", "add", "--root", str(bare_project)]) == 0
    out = capsys.readouterr().out
    assert "Available templates" in out
    assert "web  (ac)" in out
    # Review 4, batch 2.9: a bundled source's label carries its own parentheses.
    assert "  (bundled)\n" in out
    assert "((bundled))" not in out
    assert load_project_config(_config(bare_project)).templates == ("web",)
    assert not (bare_project / "loadout" / "templates").exists()


def test_bare_add_can_take_a_vendored_choice(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter(("1", "vendored"))
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert main(["template", "add", "--root", str(bare_project)]) == 0
    vendored = bare_project / "loadout" / "templates" / "web"
    assert vendored.is_dir()
    assert load_project_config(_config(bare_project)).vendored_hash("web") == tree_hash(vendored)


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        (("",), "choose a template by number (1-2)"),
        (("x",), "choose a template by number (1-2), not 'x'"),
        (("9",), "no template 9; choose 1-2"),
        (("1", ""), "choose tracked or vendored for frontend"),
    ],
)
def test_bare_add_needs_an_explicit_choice(
    bare_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: tuple[str, ...],
    message: str,
) -> None:
    """Review 3, batch 2.13: Enter took the first template with no default shown, and a
    wrong answer was a source error (exit 3) rather than a usage error."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda _: next(replies))

    assert main(["template", "add", "--root", str(bare_project)]) == 2

    assert message in capsys.readouterr().err
    assert load_project_config(_config(bare_project)).templates == ()


def test_add_changes_a_tracked_template_to_vendored(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--tracked", "--root", str(bare_project)]) == 0
    assert main(["template", "add", "web", "--vendored", "--root", str(bare_project)]) == 0
    vendored = bare_project / "loadout" / "templates" / "web"
    assert (vendored / "permissions.toml").is_file()
    config = load_project_config(_config(bare_project))
    assert config.templates == ("web",)
    assert config.vendored_hash("web") == tree_hash(vendored)


def test_add_changes_a_vendored_template_to_tracked(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--vendored", "--root", str(bare_project)]) == 0
    capsys.readouterr()
    assert main(["template", "add", "web", "--tracked", "--root", str(bare_project)]) == 0
    assert "changed web to tracked" in capsys.readouterr().out
    assert not (bare_project / "loadout" / "templates" / "web").exists()
    config = load_project_config(_config(bare_project))
    assert config.templates == ("web",)
    assert config.vendored_hash("web") is None


def test_list_reports_a_vendored_template_as_clean(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    out = capsys.readouterr().out
    assert "web" in out
    assert "vendored, clean" in out


def test_list_reports_a_modified_vendored_template(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    assert "vendored, modified" in capsys.readouterr().out


def test_list_reports_a_tracked_template_and_the_source_it_came_from(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--tracked", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    out = capsys.readouterr().out
    assert "tracked" in out
    assert "ac" in out


def test_list_reports_an_unresolved_template_without_failing(
    bare_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`list` is the command you reach for *because* something is wrong, so it
    reports a broken name rather than dying on the first one."""
    config = _config(bare_project)
    config.write_text(
        config.read_text(encoding="utf-8") + 'templates = ["gone"]\n', encoding="utf-8"
    )
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    assert "unresolved" in capsys.readouterr().out


def test_a_bare_template_command_prints_usage(bare_project: Path) -> None:
    assert main(["template"]) == 2


def test_sync_updates_an_unmodified_copy(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (upstream / "permissions.toml").write_text(
        '[shell]\nallow = ["vite", "esbuild"]\n', encoding="utf-8"
    )
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    vendored = bare_project / "loadout" / "templates" / "web" / "permissions.toml"
    assert "esbuild" in vendored.read_text(encoding="utf-8")


def test_sync_rerecords_the_hash_so_the_copy_stays_clean(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["esbuild"]\n', encoding="utf-8")
    main(["template", "sync", "web", "--root", str(bare_project)])
    config = load_project_config(_config(bare_project))
    assert config.vendored_hash("web") == tree_hash(bare_project / "loadout" / "templates" / "web")


def test_sync_carries_a_file_the_upstream_added(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (upstream / "skills" / "s").mkdir(parents=True)
    (upstream / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\n", encoding="utf-8")
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert (bare_project / "loadout" / "templates" / "web" / "skills" / "s" / "SKILL.md").is_file()


def test_sync_drops_a_file_the_upstream_removed(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    (upstream / "extra.toml").write_text("[shell]\n", encoding="utf-8")
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (upstream / "extra.toml").unlink()
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert not (bare_project / "loadout" / "templates" / "web" / "extra.toml").exists()


def test_sync_refuses_a_modified_copy_and_changes_nothing(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    vendored = bare_project / "loadout" / "templates" / "web" / "permissions.toml"
    vendored.write_text('[shell]\nallow = ["vite", "mine"]\n', encoding="utf-8")
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["esbuild"]\n', encoding="utf-8")
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 1
    assert vendored.read_text(encoding="utf-8") == '[shell]\nallow = ["vite", "mine"]\n'


def test_sync_shows_the_diff_it_refused_to_apply(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["esbuild"]\n', encoding="utf-8")
    capsys.readouterr()
    main(["template", "sync", "web", "--root", str(bare_project)])
    err = capsys.readouterr().err
    assert "mine" in err
    assert "esbuild" in err


def test_sync_reports_an_already_current_copy(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_sync_refuses_a_template_that_is_not_vendored(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--tracked", "--root", str(bare_project)])
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 3


def test_check_notes_a_modified_vendored_template_without_failing(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A vendored copy is source, so editing it is not drift — see ADR 0014."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    main(["sync", "--root", str(bare_project), "--force"])
    capsys.readouterr()
    assert main(["check", "--root", str(bare_project)]) == 0
    assert "web" in capsys.readouterr().out


def test_check_is_silent_about_a_clean_vendored_template(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project), "--force"])
    capsys.readouterr()
    assert main(["check", "--root", str(bare_project)]) == 0
    assert "modified" not in capsys.readouterr().out


def test_check_still_fails_on_real_drift_alongside_a_diverged_template(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note must not swallow the exit code check exists to produce."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project), "--force"])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    assert main(["check", "--root", str(bare_project)]) == 1


def _strip_provenance(project: Path) -> None:
    """The state `loadout harness add` used to leave behind.

    It rewrote config.toml from the harness list alone, so the
    `[template.<name>] vendored` hash was destroyed while the vendored copy
    stayed on disk. `templates` went with it, so a repo reaches the enduring
    version of this state by re-declaring the template it noticed had stopped
    merging — which is why the hash, not the declaration, is what goes missing.
    """
    path = _config(project)
    config = load_project_config(path)
    harnesses = ", ".join(f'"{h}"' for h in config.harnesses)
    names = ", ".join(f'"{n}"' for n in config.templates)
    path.write_text(f"harnesses = [{harnesses}]\ntemplates = [{names}]\n", encoding="utf-8")


def test_sync_refuses_a_copy_with_no_recorded_provenance_and_changes_nothing(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without provenance nothing can tell the user's edits from the source's, so
    refusing is the only answer that cannot destroy work. The gate used to require
    *proof of modification*, which is unavailable in exactly this state, so sync
    fell through and overwrote.

    The edit surviving is the assertion that matters: a sync that returned 1 and
    copied anyway would satisfy an exit-code check on its own.
    """
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    vendored = bare_project / "loadout" / "templates" / "web" / "permissions.toml"
    vendored.write_text('[shell]\nallow = ["vite", "mine"]\n', encoding="utf-8")
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["esbuild"]\n', encoding="utf-8")
    _strip_provenance(bare_project)

    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 1

    survived = vendored.read_text(encoding="utf-8")
    assert survived == '[shell]\nallow = ["vite", "mine"]\n'
    assert "esbuild" not in survived


def test_sync_still_self_heals_a_clean_copy_with_no_recorded_provenance(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Matching the source *is* proof the copy is unmodified, so the refusal must
    not reach a clean copy — it re-records the hash instead, which is the only way
    a repo in this state gets its provenance back."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    vendored = bare_project / "loadout" / "templates" / "web"
    _strip_provenance(bare_project)
    assert load_project_config(_config(bare_project)).vendored_hash("web") is None
    capsys.readouterr()

    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0

    assert "up to date" in capsys.readouterr().out
    assert load_project_config(_config(bare_project)).vendored_hash("web") == tree_hash(vendored)


def test_check_reports_a_vendored_copy_with_no_recorded_provenance(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`template_divergence` cannot speak about a copy it has no base for, so the
    missing hash read there as "no divergence" and check said nothing at all.
    Reported and not failed, for ADR 0014's reason: a vendored copy is source."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project), "--force"])
    _strip_provenance(bare_project)
    capsys.readouterr()

    assert main(["check", "--root", str(bare_project)]) == 0

    assert (
        "the vendored template 'web' has no recorded provenance, so nothing can tell your edits "
        "from its source — delete loadout/templates/web and run "
        "`loadout template add web --vendored` to take the source wholesale and record one"
    ) in capsys.readouterr().out


@pytest.mark.parametrize("mode", ("tracked", "vendored"))
def test_template_add_refuses_an_unloadable_project_config_before_mutation(
    tmp_path: Path, mode: str
) -> None:
    """Keys an older build wrote fail the load, and nothing is vendored or declared."""
    source = tmp_path / "loadout"
    source.mkdir()
    config = source / "config.toml"
    config.write_text('harnesses = ["claude"]\npresets = false\nartifacts = "artifacts.toml"\n')
    before = config.read_bytes()

    assert main(["template", "add", "frontend", "--" + mode, "--root", str(tmp_path)]) == 3

    assert config.read_bytes() == before
    assert not (source / "templates").exists()


@pytest.mark.parametrize("mode", ("tracked", "vendored"))
def test_template_add_refuses_an_unloadable_global_source_before_mutation(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """A global template of the same name shadows the bundled one, so the global
    manifest is read first; one that cannot load stops the command untouched."""
    (tmp_path / "loadout").mkdir()
    project_config_path(tmp_path).write_text('harnesses = ["claude"]\n')
    tree = _upstream(fake_home, monkeypatch, "frontend", '[shell]\nallow = ["npm"]\n')
    (tree.parents[2] / "loadout.toml").write_text('[[source]]\nname = "ac"\npath = "loadout"\n')
    before = project_config_path(tmp_path).read_bytes()

    assert main(["template", "add", "frontend", "--" + mode, "--root", str(tmp_path)]) == 3

    assert project_config_path(tmp_path).read_bytes() == before
    assert not vendored_path(tmp_path, "frontend").exists()


def test_an_unknown_template_names_every_one_that_resolves(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    _upstream(fake_home, monkeypatch, "company", '[shell]\nallow = ["x"]\n')

    assert main(["template", "add", "nosuch", "--vendored", "--root", str(bare_project)]) == 3

    assert capsys.readouterr().err.strip() == (
        "loadout: template 'nosuch' not found. Available: backend, company, frontend. "
        "Run 'loadout template add' to choose."
    )


def test_without_a_global_source_the_message_says_so(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_home / "no-config"))

    assert main(["template", "add", "nosuch", "--vendored", "--root", str(bare_project)]) == 3

    message = capsys.readouterr().err
    assert (
        "No global source is configured, so only vendored and bundled templates resolve." in message
    )
    assert "Available: backend, frontend." in message


def test_sync_reports_a_vendored_copy_modified_since_it_was_vendored(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review 2, 2.11: the template notes printed only in check, so a project syncing
    from a diverged copy never heard of it."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project), "--force"])
    vendored = bare_project / "loadout/templates/web/permissions.toml"
    vendored.write_text('[shell]\nallow = ["vite", "npm"]\n')
    capsys.readouterr()

    assert main(["sync", "--root", str(bare_project)]) == 0

    assert (
        "note: project.templates: the vendored template 'web' was modified after it was vendored"
        in capsys.readouterr().out
    )


def test_template_add_without_a_name_or_a_terminal_is_refused(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review 2, 2.13: the prompt read end of input and declared the first template."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    before = _config(bare_project).read_text()

    assert main(["template", "add", "--tracked", "--root", str(bare_project)]) == 2

    assert "needs a name" in capsys.readouterr().err
    assert _config(bare_project).read_text() == before


def test_template_add_without_a_mode_or_a_terminal_is_a_usage_error(
    bare_project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    assert main(["template", "add", "frontend", "--root", str(bare_project)]) == 2

    assert "needs a mode" in capsys.readouterr().err


def test_syncing_an_undeclared_template_says_it_is_not_declared(
    bare_project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review 3, batch 2.13: it claimed the template resolves from a source on every render."""
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 3

    assert capsys.readouterr().err == (
        "loadout: web is not declared in loadout/config.toml, so there is nothing to sync; "
        "run `loadout template add web` to add it\n"
    )


TOKEN = "ghp_" + "a1B2" * 9


def test_sync_never_prints_a_token_the_upstream_added(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review 4, batch 1.2: the tree diff printed it in clear."""
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (upstream / "notes.md").write_text(f"push with {TOKEN}\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0

    captured = capsys.readouterr()
    assert "+push with <redacted>\n" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_a_refused_sync_never_prints_a_token_the_upstream_added(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--vendored", "--root", str(bare_project)])
    (bare_project / "loadout/templates/web/permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    (upstream / "notes.md").write_text(f"push with {TOKEN}\n", encoding="utf-8")
    capsys.readouterr()

    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 1

    captured = capsys.readouterr()
    assert "+push with <redacted>\n" in captured.err
    assert TOKEN not in captured.out + captured.err


def test_bare_add_labels_a_vendored_copy_once(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Review 6, batch 3 (tk-avail-vendored): its label came parenthesised twice."""
    vendored = vendored_path(bare_project, "web")
    vendored.mkdir(parents=True)
    (vendored / "permissions.toml").write_text('[shell]\nallow = ["vite"]\n')
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "")

    assert main(["template", "add", "--root", str(bare_project)]) == 2

    assert "  1. web  (vendored)\n" in capsys.readouterr().out
