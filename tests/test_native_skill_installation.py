from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import loadout
import loadout.native_skill_installation as installer
from loadout.emit import check_all, render_global, write_all
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.native_skill_installation import change_native_targets, inspect_native_targets
from loadout.skill_installation import OWNER_MARKER, SourceSkillState


def _root(tmp_path: Path, *, shared: bool = False) -> Path:
    root = tmp_path / "global"
    root.mkdir()
    (root / "loadout.toml").write_text('artifacts = "artifacts.toml"\n')
    blocks = []
    for agent in ("claude", "codex"):
        source = "shared" if shared else agent
        (root / "skills" / source).mkdir(parents=True, exist_ok=True)
        blocks.append(
            f'[[artifact]]\nagents = ["{agent}"]\nformat = "tree"\ncategory = "skills"\nsource = "skills/{source}"\ndestination = "~/.{agent}/skills"\n'
        )
    (root / "artifacts.toml").write_text("\n".join(blocks))
    return root


def _bundle(path: Path, content: str = "version one") -> Path:
    path.mkdir()
    (path / "SKILL.md").write_text(
        f"---\nname: loadout\ndescription: Configure loadout.\n---\n{content}\n"
    )
    (path / "asset.bin").write_bytes(b"\x00\xff")
    return path


def _change(root: Path, bundle: Path, *, uninstall: bool = False) -> None:
    targets = inspect_native_targets(root, "default", bundle, None)
    change_native_targets(root, "default", targets, bundle, uninstall=uninstall)


@pytest.mark.parametrize("shared", [True, False])
def test_native_install_update_uninstall_and_receipts(tmp_path, fake_home, shared):
    root = _root(tmp_path, shared=shared)
    bundle = _bundle(tmp_path / "bundle")
    targets = inspect_native_targets(root, "default", bundle, None)
    assert len(targets) == (1 if shared else 2)
    assert {a for t in targets for a in t.agents} == {"claude", "codex"}
    _change(root, bundle)
    for agent in ("claude", "codex"):
        output = fake_home / f".{agent}/skills/loadout"
        assert (output / "SKILL.md").read_bytes() == (bundle / "SKILL.md").read_bytes()
        assert (output / "asset.bin").read_bytes() == b"\x00\xff"
        assert not (output / OWNER_MARKER).exists()
    assert check_all(root) == []
    assert all(
        t.location.state == SourceSkillState.INSTALLED
        for t in inspect_native_targets(root, "default", bundle, None)
    )
    second = _bundle(tmp_path / "second", "version two")
    _change(root, second)
    assert (fake_home / ".codex/skills/loadout/SKILL.md").read_bytes() == (
        second / "SKILL.md"
    ).read_bytes()
    _change(root, second, uninstall=True)
    assert check_all(root) == []
    write_all(root)
    assert not (fake_home / ".claude/skills/loadout/SKILL.md").exists()
    assert not (fake_home / ".codex/skills/loadout/SKILL.md").exists()


@pytest.mark.parametrize("uninstall", [False, True])
@pytest.mark.parametrize("modified", ["source", "output"])
def test_one_conflicting_target_preserves_every_target(tmp_path, fake_home, uninstall, modified):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    target = (
        root / "skills/codex/loadout/SKILL.md"
        if modified == "source"
        else fake_home / ".codex/skills/loadout/SKILL.md"
    )
    target.write_text("user changes\n")
    before = (root / "skills/claude/loadout/SKILL.md").read_bytes()
    second = _bundle(tmp_path / "second", "version two")
    with pytest.raises(LoadoutError, match="modified"):
        _change(root, second, uninstall=uninstall)
    assert (root / "skills/claude/loadout/SKILL.md").read_bytes() == before
    assert target.read_text() == "user changes\n"


def test_native_failure_rolls_back_sources_outputs_and_receipts(tmp_path, fake_home, monkeypatch):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    before = {p: p.read_bytes() for p in render_global(root)}
    second = _bundle(tmp_path / "second", "version two")
    original = installer.atomic_install

    def fail(path, content):
        if (
            path == fake_home / ".codex/skills/loadout/SKILL.md"
            and b"version two" in content.content
        ):
            raise OSError("interrupted write")
        original(path, content)

    monkeypatch.setattr(installer, "atomic_install", fail)
    with pytest.raises(OSError, match="interrupted"):
        _change(root, second)
    assert {p: p.read_bytes() for p in render_global(root)} == before
    assert check_all(root) == []


def test_native_cli_reports_each_source_and_installs(tmp_path, capsys):
    root = _root(tmp_path)
    config = machine_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(f'source = "{root}"\n')
    assert loadout.main(["skill", "install", "--yes"]) == 0
    capsys.readouterr()
    assert loadout.main(["skill", "status"]) == 0
    output = capsys.readouterr().out
    assert str(root / "skills/claude/loadout") in output
    assert str(root / "skills/codex/loadout") in output
    assert loadout.main(["skill", "uninstall", "--yes"]) == 0
    assert check_all(root) == []


def test_new_occupied_output_is_preflighted_before_source_changes(tmp_path, fake_home, monkeypatch):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    occupied = fake_home / ".codex/skills/loadout/SKILL.md"
    occupied.parent.mkdir(parents=True)
    occupied.write_text("user owned")

    def unexpected(*args, **kwargs):
        raise AssertionError("source mutation reached before conflict detection")

    monkeypatch.setattr(installer, "_replace_source", unexpected)
    with pytest.raises(LoadoutError, match="modified outside"):
        _change(root, bundle)
    assert occupied.read_text() == "user owned"
    assert not (root / "skills/claude/loadout").exists()


def test_native_hash_preserves_intentional_assets_and_detects_all_additions(tmp_path, fake_home):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    asset = bundle / ".hidden" / "fixture.pyc"
    asset.parent.mkdir()
    asset.write_bytes(b"intentional asset")
    _change(root, bundle)
    assert (
        fake_home / ".claude/skills/loadout/.hidden/fixture.pyc"
    ).read_bytes() == b"intentional asset"
    extra = root / "skills/codex/loadout/__pycache__/user.pyc"
    extra.parent.mkdir()
    extra.write_bytes(b"user asset")
    with pytest.raises(LoadoutError, match="modified after installation"):
        _change(root, bundle, uninstall=True)
    assert extra.read_bytes() == b"user asset"


def test_failed_rollback_retains_private_recovery_outside_native_trees(
    tmp_path, fake_home, monkeypatch
):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    second = _bundle(tmp_path / "second", "version two")
    original = installer.atomic_install
    deployed = fake_home / ".codex/skills/loadout/SKILL.md"

    def fail(path, content):
        if path == deployed and b"version two" in content.content:
            raise OSError("deployment failed")
        original(path, content)

    def rollback_fails(self):
        raise OSError("rollback failed")

    monkeypatch.setattr(installer, "atomic_install", fail)
    monkeypatch.setattr(installer._Changes, "restore", rollback_fails)
    with pytest.raises(LoadoutError, match="recovery originals retained"):
        _change(root, second)
    (recovery,) = (root / ".loadout-state").glob("skill-*/recovery.json")
    assert recovery.stat().st_mode & 0o777 == 0o600
    data = json.loads(recovery.read_text())
    assert len(data["sources"]) == 2
    for source in data["sources"]:
        assert (Path(source["quarantine"]) / "SKILL.md").read_bytes() == (
            bundle / "SKILL.md"
        ).read_bytes()
    rendered = render_global(root)
    assert len(rendered) == 4
    assert {p.name for p in rendered} == {"SKILL.md", "asset.bin"}


def test_native_and_legacy_routes_install_together(tmp_path, fake_home):
    root = _root(tmp_path)
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[[source]]\nname = "legacy"\npath = "legacy"\nuse = ["skills"]\n\n[pi]\npermissions = false\nmcp = false\nmodule-config = false\n'
    )
    (root / "legacy").mkdir()
    bundle = _bundle(tmp_path / "bundle")
    targets = inspect_native_targets(root, "default", bundle, "legacy")
    assert len(targets) == 3
    change_native_targets(root, "default", targets, bundle, uninstall=False)
    assert (fake_home / ".pi/agent/skills/loadout/SKILL.md").is_file()
    assert (fake_home / ".codex/skills/loadout/SKILL.md").read_bytes() == (
        bundle / "SKILL.md"
    ).read_bytes()
    targets = inspect_native_targets(root, "default", bundle, "legacy")
    change_native_targets(root, "default", targets, bundle, uninstall=True)
    assert not (fake_home / ".pi/agent/skills/loadout/SKILL.md").exists()
    assert not (fake_home / ".codex/skills/loadout/SKILL.md").exists()


def test_artifact_manifest_without_skills_reports_no_routes(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "artifacts.toml").write_text(
        '[[artifact]]\nagents = ["claude"]\nformat = "copy"\ncategory = "instructions"\nsource = "rules.md"\ndestination = "~/.claude/CLAUDE.md"\n'
    )
    (root / "rules.md").write_text("rules")
    assert (
        installer.run_native_skill(
            root, "default", None, command=installer.SkillCommand("install", True)
        )
        == 0
    )
    assert "No configured skill routes" in capsys.readouterr().out


def test_native_cli_finishes_global_sync_for_pending_legacy_outputs(tmp_path, fake_home):
    root = _root(tmp_path)
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text() + '\n[[source]]\nname = "legacy"\npath = "legacy"\n\n[pi]\n'
    )
    (root / "legacy").mkdir()
    permissions = root / "legacy/permissions.toml"
    permissions.write_text('[shell]\nallow = ["just test"]\n')
    config = machine_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(f'source = "{root}"\n')
    assert loadout.main(["skill", "install", "--yes"]) == 0
    assert check_all(root) == []
    permissions.write_text('[shell]\nallow = ["just build"]\n')
    assert loadout.main(["skill", "install", "--yes"]) == 0
    output = fake_home / ".pi/agent/extensions/pi-permission-system/config.json"
    assert "just build" in output.read_text()
    assert check_all(root) == []


def test_native_configuration_changes_invalidate_inspection(tmp_path):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    targets = inspect_native_targets(root, "default", bundle, None)
    manifest = root / "artifacts.toml"
    manifest.write_text(manifest.read_text().replace("~/.codex/skills", "~/.elsewhere/skills"))
    with pytest.raises(LoadoutError, match="configuration changed after preview"):
        change_native_targets(root, "default", targets, bundle, uninstall=False)
    assert not (root / "skills/claude/loadout").exists()


def _mixed_root(tmp_path: Path) -> Path:
    root = _root(tmp_path)
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text()
        + '\n[[source]]\nname = "legacy"\npath = "legacy"\nuse = ["skills"]\n\n[pi]\npermissions = false\nmcp = false\nmodule-config = false\n'
    )
    (root / "legacy").mkdir()
    return root


def _installed_files(root: Path) -> dict[Path, installer.FrozenFile | None]:
    paths = set(render_global(root))
    paths.add(root / ".loadout-state/global.json")
    for directory in ("skills", "legacy", ".loadout-bundles"):
        paths.update(path for path in (root / directory).rglob("*") if path.is_file())
    return {path: installer.read_file(path) for path in paths}


@pytest.mark.parametrize("boundary", ["deployment", "journal"])
@pytest.mark.parametrize("operation", ["update", "uninstall", "install", "new-file"])
def test_legacy_race_preserves_user_output_and_rolls_back_native_changes(
    tmp_path, fake_home, monkeypatch, boundary, operation
):
    root = _mixed_root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    if operation != "install":
        _change(root, bundle)
    before = _installed_files(root)
    second = _bundle(tmp_path / "second", "version two")
    name = "new.txt" if operation == "new-file" else "SKILL.md"
    if operation == "new-file":
        (second / name).write_text("new bundled file")
    output = fake_home / ".pi/agent/skills/loadout" / name
    injected = []

    def edit():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("concurrent user content")
        injected.append(output)

    if boundary == "deployment":
        original_apply = installer.apply_deployment

        def apply_then_edit(*args, **kwargs):
            result = original_apply(*args, **kwargs)
            edit()
            return result

        monkeypatch.setattr(installer, "apply_deployment", apply_then_edit)
    else:
        original_save = installer._Changes.save

        def save_then_edit(self):
            original_save(self)
            if self.files and self.files[-1][0] == output and not injected:
                edit()

        monkeypatch.setattr(installer._Changes, "save", save_then_edit)

    with pytest.raises(LoadoutError, match="changed"):
        _change(root, second, uninstall=operation == "uninstall")
    assert injected == [output]
    assert output.read_text() == "concurrent user content"
    assert {path: installer.read_file(path) for path in before if path != output} == {
        path: state for path, state in before.items() if path != output
    }
    if operation == "install":
        assert [
            target.location.state
            for target in inspect_native_targets(root, "default", second, None)
        ] == [SourceSkillState.MISSING] * 3
    if operation == "new-file":
        assert not (fake_home / ".codex/skills/loadout/new.txt").exists()
    assert list((root / ".loadout-state").glob("skill-*")) == []


@pytest.mark.parametrize("uninstall", [False, True])
def test_missing_legacy_output_cannot_be_occupied_during_native_deployment(
    tmp_path, fake_home, monkeypatch, uninstall
):
    root = _mixed_root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    output = fake_home / ".pi/agent/skills/loadout/SKILL.md"
    output.unlink()
    before = _installed_files(root)
    original = installer.apply_deployment
    injected = []

    def occupy(*args, **kwargs):
        result = original(*args, **kwargs)
        output.write_text("new user file")
        injected.append(output)
        return result

    monkeypatch.setattr(installer, "apply_deployment", occupy)
    with pytest.raises(LoadoutError, match="changed"):
        _change(root, _bundle(tmp_path / "second", "version two"), uninstall=uninstall)
    assert injected == [output]
    assert output.read_text() == "new user file"
    assert {path: installer.read_file(path) for path in before if path != output} == {
        path: state for path, state in before.items() if path != output
    }


@pytest.mark.parametrize("uninstall", [False, True])
def test_missing_native_source_keeps_marker_ownership_for_reinstall_and_uninstall(
    tmp_path, fake_home, uninstall
):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    target = inspect_native_targets(root, "default", bundle, None)[0]
    marker = target.metadata / OWNER_MARKER
    marker_before = marker.read_bytes()
    shutil.rmtree(target.location.path)
    _change(root, bundle, uninstall=uninstall)
    if uninstall:
        assert not marker.exists()
        assert not (fake_home / ".claude/skills/loadout/SKILL.md").exists()
    else:
        assert (target.location.path / "SKILL.md").read_bytes() == (
            bundle / "SKILL.md"
        ).read_bytes()
        assert marker.read_bytes() == marker_before
        assert all(
            target.location.state == SourceSkillState.INSTALLED
            for target in inspect_native_targets(root, "default", bundle, None)
        )
    assert check_all(root) == []


@pytest.mark.parametrize("uninstall", [False, True])
@pytest.mark.parametrize("change", ["replace", "remove", "comment"])
def test_missing_native_source_rejects_marker_changes_after_inspection(
    tmp_path, monkeypatch, uninstall, change
):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    target = inspect_native_targets(root, "default", bundle, None)[0]
    shutil.rmtree(target.location.path)
    targets = inspect_native_targets(root, "default", bundle, None)
    marker = target.metadata / OWNER_MARKER
    if change == "replace":
        marker.write_text('owner = "loadout-bundled-skill"\nhash = "sha256:user"\n')
    elif change == "remove":
        marker.unlink()
    else:
        marker.write_text(marker.read_text() + "# user metadata\n")
    before = _installed_files(root)

    def unexpected(*args, **kwargs):
        raise AssertionError("source mutation reached after ownership metadata changed")

    monkeypatch.setattr(installer, "_replace_source", unexpected)
    with pytest.raises(LoadoutError, match="changed after preview"):
        change_native_targets(root, "default", targets, bundle, uninstall=uninstall)
    assert {path: installer.read_file(path) for path in before} == before


@pytest.mark.parametrize("uninstall", [False, True])
def test_missing_native_source_does_not_claim_invalid_ownership_metadata(tmp_path, uninstall):
    root = _root(tmp_path)
    bundle = _bundle(tmp_path / "bundle")
    _change(root, bundle)
    target = inspect_native_targets(root, "default", bundle, None)[0]
    shutil.rmtree(target.location.path)
    marker = target.metadata / OWNER_MARKER
    marker.write_text("user metadata")
    before = _installed_files(root)
    with pytest.raises(LoadoutError, match="not owned"):
        _change(root, bundle, uninstall=uninstall)
    assert {path: installer.read_file(path) for path in before} == before
