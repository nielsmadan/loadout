from __future__ import annotations

import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from loadout.bundled_skill import bundled_skill_path
from loadout.errors import LoadoutError

ROOT = Path(__file__).parents[1]


def _skill(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: loadout\ndescription: Use when configuring.\n---\n",
        encoding="utf-8",
    )
    return path


def test_package_resource_is_used(tmp_path: Path) -> None:
    package = tmp_path / "src" / "loadout"
    packaged = _skill(package / "_skills" / "loadout")
    assert bundled_skill_path(package) == packaged.resolve()


def test_source_checkout_uses_the_package_resource_tree() -> None:
    assert bundled_skill_path() == (ROOT / "src" / "loadout" / "_skills" / "loadout").resolve()


def test_missing_bundle_names_package_location(tmp_path: Path) -> None:
    package = tmp_path / "src" / "loadout"
    package.mkdir(parents=True)
    with pytest.raises(LoadoutError) as caught:
        bundled_skill_path(package)
    assert str(package / "_skills" / "loadout") in str(caught.value)


def test_built_wheel_contains_the_complete_skill_tree(tmp_path: Path) -> None:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    (wheel,) = tmp_path.glob("*.whl")
    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "loadout/_skills/loadout/SKILL.md" in names
    assert "loadout/_skills/loadout/references/configuration.md" in names
