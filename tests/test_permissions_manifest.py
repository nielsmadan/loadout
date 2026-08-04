from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from loadout.errors import LoadoutError
from loadout.manifest import PermissionTarget, load_manifest

GOLDEN = Path(__file__).parent / "golden"


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "loadout.toml"
    path.write_text('[[source]]\nname = "ac"\npath = "."\n\n' + body, encoding="utf-8")
    return path


def test_golden_manifest_declares_eight_permission_targets() -> None:
    manifest = load_manifest(GOLDEN / "manifest.toml")
    assert len(manifest.permissions) == 8


def test_permission_target_fields(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "[permissions.opencode]\n"
        'output   = "opencode/opencode.json"\n'
        'render   = "opencode"\n'
        'base     = "opencode/opencode.base.json"\n'
        'preserve = ["mcp"]\n',
    )
    target = load_manifest(path).permissions[0]
    assert target == PermissionTarget(
        name="opencode",
        path=PurePosixPath("opencode/opencode.json"),
        renderer="opencode",
        base=PurePosixPath("opencode/opencode.base.json"),
        preserve=("mcp",),
        select_all=True,
    )


def test_base_and_preserve_default_to_empty(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.pi]\noutput = "pi/permissions.json"\nrender = "pi"\n',
    )
    target = load_manifest(path).permissions[0]
    assert target.base is None
    assert target.preserve == ()
    assert target.select_all is True


def test_empty_rules_list_selects_nothing(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.a]\noutput = "a.json"\nrender = "claude"\nrules = []\n',
    )
    assert load_manifest(path).permissions[0].select_all is False


def test_non_empty_rules_list_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.a]\noutput = "a.json"\nrender = "claude"\nrules = ["shell"]\n',
    )
    with pytest.raises(LoadoutError, match="milestone 4"):
        load_manifest(path)


def test_missing_render_key_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '[permissions.a]\noutput = "a.json"\n')
    with pytest.raises(LoadoutError, match="render"):
        load_manifest(path)


def test_output_escaping_the_root_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.a]\noutput = "../escaped.json"\nrender = "pi"\n',
    )
    with pytest.raises(LoadoutError):
        load_manifest(path)


def test_absolute_output_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, '[permissions.a]\noutput = "/tmp/x.json"\nrender = "pi"\n')
    with pytest.raises(LoadoutError):
        load_manifest(path)


def test_output_collision_with_an_instruction_target_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[instructions.claude]\noutput = "shared.md"\norder = ["x"]\n\n'
        '[permissions.a]\noutput = "shared.md"\nrender = "pi"\n',
    )
    with pytest.raises(LoadoutError, match="already claimed"):
        load_manifest(path)


def test_two_permission_targets_sharing_an_output_are_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.a]\noutput = "same.json"\nrender = "pi"\n\n'
        '[permissions.b]\noutput = "same.json"\nrender = "claude"\n',
    )
    with pytest.raises(LoadoutError, match="already claimed"):
        load_manifest(path)


def test_permissions_only_manifest_is_accepted(tmp_path: Path) -> None:
    path = write(tmp_path, '[permissions.pi]\noutput = "p.json"\nrender = "pi"\n')
    manifest = load_manifest(path)
    assert manifest.targets == ()
    assert len(manifest.permissions) == 1


def test_manifest_with_no_targets_of_either_kind_is_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "")
    with pytest.raises(LoadoutError, match=r"no .* targets"):
        load_manifest(path)


def test_base_pointing_at_a_generated_output_is_rejected(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.claude]\noutput = "claude/settings.json"\nrender = "claude"\n'
        'base   = "claude/settings.json"\n',
    )
    with pytest.raises(LoadoutError, match="generated output"):
        load_manifest(path)


def test_preserve_must_be_a_list_of_strings(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[permissions.a]\noutput = "a.json"\nrender = "pi"\npreserve = "mcp"\n',
    )
    with pytest.raises(LoadoutError, match="preserve"):
        load_manifest(path)
