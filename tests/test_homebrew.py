import importlib.util
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("homebrew", ROOT / "scripts/homebrew.py")
homebrew = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(homebrew)


def test_formula_uses_release_source_and_locked_runtime_dependency(tmp_path):
    output = tmp_path / "Formula/loadout.rb"
    digest = "a" * 64
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/homebrew.py"), "9.8.7", digest, str(output)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    formula = output.read_text()
    assert 'url "https://github.com/nielsmadan/loadout/archive/refs/tags/v9.8.7.tar.gz"' in formula
    assert f'\n  sha256 "{digest}"\n' in formula
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    dependency = next(package for package in lock["package"] if package["name"] == "tomlkit")
    assert f'    url "{dependency["sdist"]["url"]}"' in formula
    assert f'    sha256 "{dependency["sdist"]["hash"].removeprefix("sha256:")}"' in formula
    assert re.findall(r'^  resource "([^"]+)" do$', formula, flags=re.MULTILINE) == ["tomlkit"]


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
