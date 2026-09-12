import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILES = {"pyproject.toml": "version", "src/loadout/__init__.py": "__version__"}
CURRENT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.fixture
def release_tree(tmp_path):
    for filename in [*VERSION_FILES, "uv.lock"]:
        path = tmp_path / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / filename).read_bytes())
    return tmp_path


def invoke(root, *args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/prepare_release.py"), *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def test_updates_both_versions_without_changing_other_content(release_tree):
    originals = {name: (release_tree / name).read_text() for name in VERSION_FILES}
    result = invoke(release_tree, "9.8.7")
    assert result.returncode == 0, result.stderr
    for name, field in VERSION_FILES.items():
        expected = "\n".join(
            f'{field} = "9.8.7"' if line.startswith(f"{field} = ") else line
            for line in originals[name].split("\n")
        )
        assert (release_tree / name).read_text() == expected


def test_malformed_runtime_version_preserves_project_file(release_tree):
    path = release_tree / "src/loadout/__init__.py"
    path.write_text('unrelated = "value"\n')
    original = (release_tree / "pyproject.toml").read_bytes()
    result = invoke(release_tree, "9.8.7")
    assert result.returncode == 1
    assert "Expected one __version__" in result.stderr
    assert (release_tree / "pyproject.toml").read_bytes() == original
    assert path.read_text() == 'unrelated = "value"\n'


@pytest.mark.parametrize("version", ["v1.2.3", "1.2", "1.02.3", "1.2.3; touch bad"])
def test_invalid_version_preserves_source(release_tree, version):
    original = {name: (release_tree / name).read_bytes() for name in VERSION_FILES}
    result = invoke(release_tree, version)
    assert result.returncode == 1
    assert "three-part release version" in result.stderr
    assert {name: (release_tree / name).read_bytes() for name in VERSION_FILES} == original


def test_check_validates_version_without_writes(release_tree):
    original = {name: (release_tree / name).read_bytes() for name in [*VERSION_FILES, "uv.lock"]}
    result = invoke(release_tree, CURRENT_VERSION, "--check")
    assert result.returncode == 0, result.stderr
    assert {name: (release_tree / name).read_bytes() for name in original} == original


@pytest.mark.parametrize("filename", [*VERSION_FILES, "uv.lock"])
def test_check_rejects_a_version_mismatch(release_tree, filename):
    path = release_tree / filename
    path.write_text(path.read_text().replace(f'"{CURRENT_VERSION}"', '"9.8.7"'))
    result = invoke(release_tree, CURRENT_VERSION, "--check")
    assert result.returncode == 1
    assert filename in result.stderr


def test_current_package_and_lock_versions_agree():
    result = invoke(ROOT, CURRENT_VERSION, "--check")
    assert result.returncode == 0, result.stderr
