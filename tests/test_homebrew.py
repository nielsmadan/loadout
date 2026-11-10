import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("homebrew", ROOT / "scripts/homebrew.py")
homebrew = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(homebrew)


def test_formula_uses_release_source_and_locked_runtime_dependencies(tmp_path):
    output = tmp_path / "Formula/loadout.rb"
    digest = "a" * 64
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/homebrew.py"), "render", "9.8.7", digest, str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    formula = output.read_text()
    assert 'url "https://github.com/nielsmadan/loadout/archive/refs/tags/v9.8.7.tar.gz"' in formula
    assert f'\n  sha256 "{digest}"\n' in formula
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    for name in ("shtab", "tomlkit"):
        dependency = next(package for package in lock["package"] if package["name"] == name)
        assert f'    url "{dependency["sdist"]["url"]}"' in formula
        assert f'    sha256 "{dependency["sdist"]["hash"].removeprefix("sha256:")}"' in formula
    resources = re.findall(r'^  resource "([^"]+)" do$', formula, flags=re.MULTILINE)
    assert resources == ["shtab", "tomlkit"]


def package(name, dependencies=()):
    return {
        "name": name,
        "dependencies": [{"name": dependency} for dependency in dependencies],
        "sdist": {"url": f"https://example.com/{name}.tar.gz", "hash": "sha256:" + "b" * 64},
    }


def test_runtime_resources_include_shared_transitive_dependencies_once():
    lock = {
        "package": [
            package("loadout", ["first", "second"]),
            package("first", ["shared"]),
            package("second", ["shared"]),
            package("shared"),
            package("development-only"),
        ]
    }
    resources = homebrew.resources(lock)
    assert re.findall(r'^  resource "([^"]+)" do$', resources, flags=re.MULTILINE) == [
        "first",
        "second",
        "shared",
    ]


def test_ambiguous_dependency_requires_explicit_handling():
    lock = {"package": [package("loadout", ["dep"]), package("dep"), package("dep")]}
    with pytest.raises(ValueError, match="Expected one locked package for dep"):
        homebrew.resources(lock)


def test_conditional_dependency_requires_explicit_handling():
    root = package("loadout", ["dep"])
    root["dependencies"][0]["marker"] = "python_version < '3.14'"
    with pytest.raises(ValueError, match="Conditional dependency"):
        homebrew.resources({"package": [root, package("dep")]})


def test_missing_source_archive_fails_instead_of_omitting_dependency():
    dependency = package("dep")
    del dependency["sdist"]
    with pytest.raises(ValueError, match="checksummed source archive for dep"):
        homebrew.resources({"package": [package("loadout", ["dep"]), dependency]})


@pytest.mark.parametrize("version,digest", [("v1.2.3", "a" * 64), ("1.2.3", "invalid")])
def test_invalid_release_identity_is_rejected(version, digest):
    with pytest.raises(ValueError):
        homebrew.render(version, digest, ROOT)


def formula_for(version):
    return homebrew.render(version, "a" * 64, ROOT)


@pytest.mark.parametrize(
    "existing,expected_output,expected_version",
    [
        (None, "updated", "1.2.3"),
        ("1.2.2", "updated", "1.2.3"),
        ("1.2.3", "updated", "1.2.3"),
        ("1.10.0", "skipped-newer", "1.10.0"),
    ],
)
def test_publish_never_downgrades_the_tap(tmp_path, existing, expected_output, expected_version):
    formula = tmp_path / "loadout.rb"
    formula.write_text(formula_for("1.2.3"))
    tap_formula = tmp_path / "tap/Formula/loadout.rb"
    if existing is not None:
        tap_formula.parent.mkdir(parents=True)
        tap_formula.write_text(formula_for(existing))
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/homebrew.py"),
            "publish",
            str(formula),
            str(tap_formula),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected_output}\n"
    assert tap_formula.read_text() == formula_for(expected_version)


def test_publish_rejects_an_unrecognised_tap_formula(tmp_path):
    formula = tmp_path / "loadout.rb"
    formula.write_text(formula_for("1.2.3"))
    tap_formula = tmp_path / "tap.rb"
    unrecognised = 'class Loadout < Formula\n  url "https://example.com/loadout.tar.gz"\nend\n'
    tap_formula.write_text(unrecognised)
    with pytest.raises(ValueError, match="one Loadout release version"):
        homebrew.publish(formula, tap_formula)
    assert tap_formula.read_text() == unrecognised
