from pathlib import Path

import pytest

from loadout.deployment import read_file
from loadout.emit import render_all, write_all
from loadout.errors import LoadoutError
from loadout.manifest import load_manifest
from loadout.templates import resolve_template
from test_artifacts import write


def declared_catalog(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    library = root / "library"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "machine"))
    write(root, "machine/loadout/config.toml", f'source="{library}"\n')
    write(library, "loadout.toml", 'extends="middle"\n')
    write(library, "middle.toml", 'extends="base"\n')
    base = write(
        library,
        "base.toml",
        '[[source]]\nname="catalog"\npath="."\nuse=["templates"]\n'
        '[instructions.pi]\noutput="unused.md"\norder=[]\n',
    )
    write(library, "templates/web.toml", "")
    return base


def copy_route(destination: Path, root: Path, scope: str) -> str:
    route = (
        f'output="{destination.relative_to(root)}"'
        if scope == "project"
        else f'destination="{destination}"'
    )
    return (
        '[[artifact]]\nagents=["pi"]\nformat="copy"\ncategory="support"\n'
        f'source="replacement.toml"\n{route}\n'
    )


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("force", [False, True])
def test_inherited_template_manifest_cannot_be_adopted_as_an_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, force: bool
) -> None:
    base = declared_catalog(tmp_path, monkeypatch)
    config = 'harnesses=["pi"]\npresets=false\ntemplates=["web"]\n'
    if scope == "project":
        config += 'artifacts="artifacts.toml"\n'
        source = tmp_path / "loadout"
    else:
        source = tmp_path
        write(tmp_path, "loadout.toml", 'artifacts="artifacts.toml"\n')
    write(tmp_path, "loadout/config.toml", config)
    write(source, "artifacts.toml", copy_route(base, tmp_path, scope))
    replacement = write(source, "replacement.toml", base.read_bytes())
    replacement.chmod(base.stat().st_mode)
    assert read_file(replacement) == read_file(base)
    assert resolve_template("web", tmp_path).source == "catalog"
    with pytest.raises(LoadoutError, match="overlaps source"):
        render_all(tmp_path)
    with pytest.raises(LoadoutError, match="overlaps source"):
        write_all(tmp_path, force=force)
    assert base.read_bytes() == replacement.read_bytes()


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("force", [False, True])
def test_retirement_preserves_a_newly_inherited_template_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, force: bool
) -> None:
    base = declared_catalog(tmp_path, monkeypatch)
    config = 'harnesses=["pi"]\npresets=false\n'
    if scope == "project":
        config += 'artifacts="artifacts.toml"\n'
        source = tmp_path / "loadout"
    else:
        source = tmp_path
        write(tmp_path, "loadout.toml", 'artifacts="artifacts.toml"\n')
    config_path = write(tmp_path, "loadout/config.toml", config)
    index = write(source, "artifacts.toml", copy_route(base, tmp_path, scope))
    replacement = write(source, "replacement.toml", base.read_bytes())
    replacement.chmod(base.stat().st_mode)
    write_all(tmp_path)
    assert read_file(base) == read_file(replacement)
    index.write_text("")
    config_path.write_text(config + 'templates=["web"]\n')
    assert resolve_template("web", tmp_path).source == "catalog"
    assert base.resolve() in load_manifest(base.parent / "loadout.toml").config_paths
    assert render_all(tmp_path) == {}
    with pytest.raises(LoadoutError, match="overlaps source"):
        write_all(tmp_path, force=force)
    assert base.read_bytes() == replacement.read_bytes()
