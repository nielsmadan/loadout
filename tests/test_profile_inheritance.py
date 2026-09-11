from pathlib import Path

import pytest

from loadout.composition import render
from loadout.emit import check_all, render_global, write_all
from loadout.errors import LoadoutError
from loadout.manifest import load_manifest, load_profile


def profile_root(root: Path, declarations: str) -> Path:
    (root / "loadout.toml").write_text('[[source]]\nname = "local"\npath = "."\n' + declarations)
    return root


def test_legacy_target_inherits_fields_and_applies_substitution(tmp_path: Path) -> None:
    root = profile_root(
        tmp_path,
        '[instructions.claude]\noutput = "out.md"\n'
        'destinations = ["~/CLAUDE.md"]\norder = ["intro", "policy"]\n'
        '[instructions.shared]\noutput = "shared.md"\norder = ["intro"]\n',
    )
    (root / "variant.toml").write_text(
        'extends = "default"\n[instructions.claude]\nsubstitute = {policy = "variant"}\n'
    )
    fragments = root / "instructions"
    fragments.mkdir()
    for name in ("intro", "policy", "variant"):
        (fragments / f"{name}.md").write_text(name)
    manifest = load_profile(root, "variant")
    target, shared = manifest.targets
    assert str(target.path) == "out.md"
    assert tuple(map(str, target.destinations)) == ("~/CLAUDE.md",)
    assert target.fragments == ("intro", "policy")
    assert render(target, manifest).endswith("intro\n\nvariant\n")
    assert str(shared.path) == "shared.md"
    assert load_profile(root).targets[0].substitute == ()
    assert load_manifest(root / "variant.toml") == manifest


def test_explicit_manifest_path_remains_the_start_of_inheritance(tmp_path: Path) -> None:
    root = profile_root(tmp_path, '[instructions.claude]\noutput="out.md"\norder=["intro"]\n')
    explicit = root / "default.toml"
    explicit.write_text('extends="default"\n[instructions.claude]\norder=["personal"]\n')
    manifest = load_manifest(explicit)
    assert manifest.targets[0].fragments == ("personal",)
    assert str(manifest.targets[0].path) == "out.md"
    assert manifest.config_paths == (explicit, root / "loadout.toml")


@pytest.mark.parametrize(
    "parent", ["", ".", "..", "../base", "./base", "nested/base", "/base", r"..\base"]
)
def test_parent_must_name_a_sibling_profile(tmp_path: Path, parent: str) -> None:
    root = profile_root(tmp_path, '[instructions.pi]\noutput="out.md"\norder=[]\n')
    (root / "variant.toml").write_text(f"extends='{parent}'\n")
    with pytest.raises(LoadoutError, match="profile name must name a sibling file"):
        load_profile(root, "variant")


def test_symlinked_parent_must_remain_in_the_source_directory(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (tmp_path / "base.toml").write_text('artifacts="artifacts.toml"\n')
    (root / "base.toml").symlink_to(tmp_path / "base.toml")
    (root / "loadout.toml").write_text('extends="base"\n')
    with pytest.raises(LoadoutError, match="profile manifest escapes source root"):
        load_profile(root)


def test_symlinked_parent_cycle_uses_canonical_identity(tmp_path: Path) -> None:
    (tmp_path / "loadout.toml").write_text('extends="alias"\n')
    (tmp_path / "alias.toml").symlink_to(tmp_path / "loadout.toml")
    with pytest.raises(LoadoutError, match="extends cycle: default -> default"):
        load_profile(tmp_path)


def test_symlinked_parent_dependency_is_protected_at_its_real_path(tmp_path: Path) -> None:
    manifest = tmp_path / "loadout.toml"
    manifest.write_text('extends="alias"\n')
    base = tmp_path / "base.toml"
    base.write_text('artifacts="artifacts.toml"\n')
    (tmp_path / "alias.toml").symlink_to(base)
    (tmp_path / "replacement.toml").write_bytes(base.read_bytes())
    (tmp_path / "artifacts.toml").write_text(
        '[[artifact]]\nagents=["pi"]\nformat="copy"\ncategory="support"\n'
        f'destination="{base}"\nsource="replacement.toml"\n'
    )
    loaded = load_profile(tmp_path)
    assert loaded.config_paths == (manifest.resolve(), base.resolve())
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_global(tmp_path)
    with pytest.raises(LoadoutError, match="overlaps source"):
        write_all(tmp_path)
    assert base.read_bytes() == b'artifacts="artifacts.toml"\n'


def test_permission_target_inherits_renderer_and_replaces_lists(tmp_path: Path) -> None:
    root = profile_root(
        tmp_path,
        '[permissions.cli]\noutput = "rules.txt"\nrender = "codex"\n'
        'preserve = ["first", "second"]\n',
    )
    (root / "variant.toml").write_text(
        'extends = "default"\n[permissions.cli]\npreserve = []\nrules = []\n'
    )
    target = load_profile(root, "variant").permissions[0]
    assert (str(target.path), target.renderer, target.preserve, target.select_all) == (
        "rules.txt",
        "codex",
        (),
        False,
    )


def test_removals_apply_before_child_fields_in_a_chain(tmp_path: Path) -> None:
    root = profile_root(
        tmp_path,
        '[instructions."with.dot"]\noutput = "out.md"\n'
        'destinations = ["~/CLAUDE.md"]\norder = ["intro"]\n'
        'substitute = {intro = "base", policy = "shared"}\n',
    )
    (root / "middle.toml").write_text(
        'extends = "default"\nremove = [\'instructions."with.dot".output\', '
        "'instructions.\"with.dot\".substitute.policy']\n"
    )
    (root / "variant.toml").write_text(
        'extends = "middle"\n[instructions."with.dot"]\norder = []\n'
    )
    manifest = load_profile(root, "variant")
    target = manifest.targets[0]
    assert target.path is None
    assert tuple(map(str, target.destinations)) == ("~/CLAUDE.md",)
    assert target.fragments == ()
    assert target.substitute == (("intro", "base"),)
    assert tuple(p.name for p in manifest.config_paths) == (
        "variant.toml",
        "middle.toml",
        "loadout.toml",
    )
    assert load_profile(root).targets[0].substitute == (("intro", "base"), ("policy", "shared"))


def test_removing_a_target_allows_wholesale_replacement(tmp_path: Path) -> None:
    root = profile_root(
        tmp_path,
        '[instructions.claude]\noutput = "old.md"\n'
        'destinations = ["~/CLAUDE.md"]\norder = ["old"]\n',
    )
    (root / "variant.toml").write_text(
        'extends = "default"\nremove = ["instructions.claude"]\n'
        '[instructions.claude]\noutput = "new.md"\norder = ["new"]\n'
    )
    target = load_profile(root, "variant").targets[0]
    assert (str(target.path), target.fragments, target.destinations) == ("new.md", ("new",), ())


def test_agent_field_removal_exposes_all_and_maps_replace(tmp_path: Path) -> None:
    root = profile_root(
        tmp_path,
        '[all]\ninstructions = ["shared"]\n[pi]\ninstructions = ["own"]\n'
        'substitute = {shared = "base", own = "base-own"}\n',
    )
    (root / "variant.toml").write_text(
        'extends = "default"\nremove = ["pi.instructions"]\n'
        '[pi]\nsubstitute = {shared = "variant"}\n'
    )
    target = load_profile(root, "variant").targets[0]
    assert target.fragments == ("shared",)
    assert target.substitute == (("shared", "variant"),)


@pytest.mark.parametrize(
    "removal, message",
    [
        ('["pi.missing"]', "does not exist"),
        ('["pi", "pi.instructions"]', "overlap"),
        ('["pi", "pi"]', "overlap"),
        ('["pi.instructions.0"]', "table"),
        ('"pi"', "list"),
        ("[12]", "strings"),
    ],
)
def test_invalid_removals_are_refused(tmp_path: Path, removal: str, message: str) -> None:
    root = profile_root(tmp_path, '[pi]\ninstructions = ["intro"]\n')
    (root / "variant.toml").write_text(f'extends = "default"\nremove = {removal}\n')
    with pytest.raises(LoadoutError, match=message):
        load_profile(root, "variant")


def test_removal_without_parent_is_refused(tmp_path: Path) -> None:
    root = profile_root(tmp_path, "[pi]\n")
    manifest = root / "loadout.toml"
    manifest.write_text('remove = ["pi"]\n' + manifest.read_text())
    with pytest.raises(LoadoutError, match=r"requires.*extends"):
        load_profile(root)


def test_profile_indexes_reuse_shared_inputs_through_sync(tmp_path: Path, fake_home: Path) -> None:
    (tmp_path / "loadout.toml").write_text('artifacts = "default-routes.toml"\n')
    (tmp_path / "variant.toml").write_text(
        'extends = "default"\nartifacts = "variant-routes.toml"\n'
    )
    route = (
        '[[artifact]]\nagents=["claude"]\ndestination="~/CLAUDE.md"\n'
        'format="text"\ncategory="instructions"\nmerge="concat"\n'
        'sources=[{source="shared.md"},{source="OVERLAY.md"}]\n'
    )
    for name in ("default", "variant"):
        (tmp_path / f"{name}-routes.toml").write_text(route.replace("OVERLAY", name))
        (tmp_path / f"{name}.md").write_text(name)
    (tmp_path / "shared.md").write_text("shared")
    output = fake_home / "CLAUDE.md"
    write_all(tmp_path)
    assert output.read_bytes() == b"shared\n\ndefault\n"
    write_all(tmp_path, "variant")
    assert output.read_bytes() == b"shared\n\nvariant\n"
    assert check_all(tmp_path, "variant") == []
    write_all(tmp_path)
    assert output.read_bytes() == b"shared\n\ndefault\n"
