from itertools import product
from pathlib import Path

import pytest

import loadout
import loadout.native_skill_installation as native
import loadout.skill_installation as legacy
from loadout.emit import write_all
from test_cli import _global_skill_root, _write_machine_config
from test_skill_installation import _bundle


def _use_bundle(monkeypatch: pytest.MonkeyPatch, bundle: Path) -> None:
    monkeypatch.setattr("loadout.commands.bundled_skill_path", lambda: bundle)
    monkeypatch.setattr(native, "bundled_skill_path", lambda: bundle)


def _override_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, mixed: bool) -> Path:
    root = _global_skill_root(tmp_path, monkeypatch, sources=("company", "personal"))
    manifest = root / "loadout.toml"
    if mixed:
        manifest.write_text('artifacts="artifacts.toml"\n' + manifest.read_text())
        (root / "native-skills").mkdir()
        (root / "artifacts.toml").write_text(
            '[[artifact]]\nagents=["pi"]\nformat="tree"\ncategory="skills"\n'
            'source="native-skills"\ndestination="~/.native/skills"\n'
        )
    _use_bundle(monkeypatch, _bundle(tmp_path / "first", "version one\n"))
    assert loadout.main(["skill", "install", "--source", "personal", "--yes"]) == 0
    _bundle(root / "company/skills/loadout", "company version\n")
    for source in ("company", "personal"):
        review = root / source / "skills/review"
        review.mkdir()
        (review / "SKILL.md").write_text(f"{source} review\n")
    retained = root / "company/skills/retained"
    retained.mkdir()
    (retained / "SKILL.md").write_text("retained company skill\n")
    manifest.write_text(
        manifest.read_text().replace(
            'path = "personal"',
            'path = "personal"\n[source.overrides]\n'
            'skills = ["loadout", "review"] # retain this comment',
        )
    )
    write_all(root)
    return root


@pytest.mark.parametrize("case", tuple(product((False, True), repeat=3)))
def test_skill_commands_manage_the_active_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[bool, bool, bool],
) -> None:
    mixed, explicit, inherited = case
    root = _override_root(tmp_path, monkeypatch, mixed=mixed)
    if inherited:
        (root / "work.toml").write_text('extends="default"\n')
        _write_machine_config(tmp_path / "xdg", root, "work")
    source_args = ["--source", "personal"] if explicit else []
    capsys.readouterr()
    assert loadout.main(["skill", "status", *source_args]) == 0
    status = capsys.readouterr().out
    assert "source personal:" in status
    assert "(installed)" in status
    second = _bundle(tmp_path / "second", "version two\n")
    _use_bundle(monkeypatch, second)
    assert loadout.main(["skill", "status", *source_args]) == 0
    assert "(update available)" in capsys.readouterr().out
    assert loadout.main(["skill", "install", *source_args, "--yes"]) == 0
    winner = root / "personal/skills/loadout"
    assert (winner / "SKILL.md").read_bytes() == (second / "SKILL.md").read_bytes()
    output = fake_home / ".claude/skills/loadout/SKILL.md"
    assert output.read_text().endswith("version two\n")
    manifest = root / "loadout.toml"
    before = manifest.read_text()
    manifest.chmod(0o640)
    assert loadout.main(["skill", "uninstall", *source_args, "--yes"]) == 0
    assert not winner.exists()
    assert manifest.read_text() == before.replace('["loadout", "review"]', '["review"]')
    assert manifest.stat().st_mode & 0o777 == 0o640
    if inherited:
        assert (root / "work.toml").read_text() == 'extends="default"\n'
    assert (root / "company/skills/loadout/SKILL.md").read_text().endswith("company version\n")
    assert output.read_text().endswith("company version\n")
    assert (fake_home / ".claude/skills/review/SKILL.md").read_text().endswith("personal review\n")
    assert (
        (fake_home / ".claude/skills/retained/SKILL.md")
        .read_text()
        .endswith("retained company skill\n")
    )
    if mixed:
        assert [
            path for path in (fake_home / ".native/skills/loadout").rglob("*") if path.is_file()
        ] == []
    assert loadout.main(["check", "--global"]) == 0


@pytest.mark.parametrize("case", tuple(product((False, True), ("modified", "unowned"))))
def test_skill_override_preserves_source_ownership_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
    case: tuple[bool, str],
) -> None:
    mixed, state = case
    root = _override_root(tmp_path, monkeypatch, mixed=mixed)
    winner = root / "personal/skills/loadout"
    if state == "modified":
        (winner / "SKILL.md").write_text("user edits\n")
    else:
        (winner / legacy.OWNER_MARKER).unlink()
    manifest = (root / "loadout.toml").read_bytes()
    source = (winner / "SKILL.md").read_bytes()
    output = fake_home / ".claude/skills/loadout/SKILL.md"
    rendered = output.read_bytes()
    for action in ("install", "uninstall"):
        capsys.readouterr()
        assert loadout.main(["skill", action, "--yes"]) == 1
        error = capsys.readouterr().err
        assert ("modified" if state == "modified" else "not owned") in error
        assert (winner / "SKILL.md").read_bytes() == source
        assert output.read_bytes() == rendered
        assert (root / "loadout.toml").read_bytes() == manifest


def test_explicit_source_cannot_manage_a_shadowed_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _override_root(tmp_path, monkeypatch, mixed=False)
    for action in ("status", "install", "uninstall"):
        flags = [] if action == "status" else ["--yes"]
        assert loadout.main(["skill", action, "--source", "company", *flags]) == 3
        assert "does not select the active skill" in capsys.readouterr().err
    assert (root / "company/skills/loadout/SKILL.md").read_text().endswith("company version\n")


@pytest.mark.parametrize("mixed", [False, True])
def test_override_uninstall_rolls_back_a_failed_manifest_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_home: Path, mixed: bool
) -> None:
    root = _override_root(tmp_path, monkeypatch, mixed=mixed)
    manifest = root / "loadout.toml"
    before = manifest.read_bytes()
    winner = root / "personal/skills/loadout/SKILL.md"
    source = winner.read_bytes()
    output = fake_home / ".claude/skills/loadout/SKILL.md"
    rendered = output.read_bytes()
    installer = native if mixed else legacy
    original = installer.atomic_install

    def fail(path: Path, content: legacy.FrozenFile) -> None:
        if path == manifest and content.content != before:
            original(path, content)
            raise OSError("manifest write failed")
        original(path, content)

    monkeypatch.setattr(installer, "atomic_install", fail)
    assert loadout.main(["skill", "uninstall", "--yes"]) == 4
    assert manifest.read_bytes() == before
    assert winner.read_bytes() == source
    assert output.read_bytes() == rendered
    assert loadout.main(["check", "--global"]) == 0
