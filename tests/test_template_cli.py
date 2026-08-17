from __future__ import annotations

from pathlib import Path

import pytest

from loadout.cli import main
from loadout.project import load_project_config, project_config_path
from loadout.templates import tree_hash


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


def test_add_declares_without_copying_anything(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "add", "web", "--root", str(bare_project)]) == 0
    assert load_project_config(_config(bare_project)).templates == ("web",)
    assert not (bare_project / "loadout" / "templates").exists()


def test_add_refuses_a_name_that_does_not_resolve(bare_project: Path) -> None:
    assert main(["template", "add", "web", "--root", str(bare_project)]) == 3
    assert load_project_config(_config(bare_project)).templates == ()


def test_vendor_copies_the_tree_and_records_the_hash(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    assert main(["template", "vendor", "web", "--root", str(bare_project)]) == 0

    vendored = bare_project / "loadout" / "templates" / "web"
    assert (vendored / "permissions.toml").read_text(encoding="utf-8") == (
        '[shell]\nallow = ["vite"]\n'
    )
    config = load_project_config(_config(bare_project))
    assert config.templates == ("web",)
    assert config.vendored_hash("web") == tree_hash(vendored)


def test_vendor_refuses_when_a_copy_is_already_there(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
    assert main(["template", "vendor", "web", "--root", str(bare_project)]) == 3


def test_list_reports_a_vendored_template_as_clean(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    capsys.readouterr()
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    assert "vendored, modified" in capsys.readouterr().out


def test_list_reports_a_declared_template_and_the_source_it_came_from(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["template", "list", "--root", str(bare_project)]) == 0
    out = capsys.readouterr().out
    assert "declared" in out
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["esbuild"]\n', encoding="utf-8")
    main(["template", "sync", "web", "--root", str(bare_project)])
    config = load_project_config(_config(bare_project))
    assert config.vendored_hash("web") == tree_hash(bare_project / "loadout" / "templates" / "web")


def test_sync_carries_a_file_the_upstream_added(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
    (upstream / "skills" / "s").mkdir(parents=True)
    (upstream / "skills" / "s" / "SKILL.md").write_text("---\nname: s\n---\n", encoding="utf-8")
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert (bare_project / "loadout" / "templates" / "web" / "skills" / "s" / "SKILL.md").is_file()


def test_sync_drops_a_file_the_upstream_removed(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    (upstream / "extra.toml").write_text("[shell]\n", encoding="utf-8")
    main(["template", "vendor", "web", "--root", str(bare_project)])
    (upstream / "extra.toml").unlink()
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert not (bare_project / "loadout" / "templates" / "web" / "extra.toml").exists()


def test_sync_refuses_a_modified_copy_and_changes_nothing(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 0
    assert "up to date" in capsys.readouterr().out


def test_sync_refuses_a_template_that_is_not_vendored(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "add", "web", "--root", str(bare_project)])
    assert main(["template", "sync", "web", "--root", str(bare_project)]) == 3


def test_check_notes_a_modified_vendored_template_without_failing(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A vendored copy is source, so editing it is not drift — see ADR 0014."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
    (bare_project / "loadout" / "templates" / "web" / "permissions.toml").write_text(
        '[shell]\nallow = ["mine"]\n', encoding="utf-8"
    )
    main(["sync", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project)])
    capsys.readouterr()
    assert main(["check", "--root", str(bare_project)]) == 0
    assert "modified" not in capsys.readouterr().out


def test_check_still_fails_on_real_drift_alongside_a_diverged_template(
    bare_project: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The note must not swallow the exit code check exists to produce."""
    _upstream(fake_home, monkeypatch, "web", '[shell]\nallow = ["vite"]\n')
    main(["template", "vendor", "web", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
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
    main(["template", "vendor", "web", "--root", str(bare_project)])
    main(["sync", "--root", str(bare_project)])
    _strip_provenance(bare_project)
    capsys.readouterr()

    assert main(["check", "--root", str(bare_project)]) == 0

    assert "no recorded provenance" in capsys.readouterr().out
