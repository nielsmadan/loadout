"""`kit add` names each project skill the kit already supplies byte for byte.

dreamhack/app1 had hand-copied `rn-upgrade` into the project before the react-native kit
offered it. A project skill replaces the kit's whole, so the copy keeps rendering, and
stops following the kit. `kit add` points at the deletion and never makes it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from loadout.cli import main
from loadout.commands import identical_skill_note

pytestmark = pytest.mark.integration


def _catalog(fake_home: Path, monkeypatch: pytest.MonkeyPatch, project: Path) -> Path:
    """The global source's `rn` manifest offering a copy of the project's `probe` skill and
    an edited copy of it under another name."""
    source = fake_home / "ac"
    kits = source / "loadout" / "kits"
    kits.mkdir(parents=True)
    (kits / "rn.toml").write_text('skills = ["probe", "edited"]\n', encoding="utf-8")
    own = project / "loadout" / "skills" / "probe"
    shutil.copytree(own, kits / "skills" / "probe")
    shutil.copytree(own, project / "loadout" / "skills" / "edited")
    shutil.copytree(own, kits / "skills" / "edited")
    (kits / "skills" / "edited" / "reference.md").write_text("Changed.\n", encoding="utf-8")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "ac"\npath = "loadout"\n\n[claude]\ninstructions = []\n',
        encoding="utf-8",
    )
    xdg = fake_home / ".config"
    (xdg / "loadout").mkdir(parents=True, exist_ok=True)
    (xdg / "loadout" / "config.toml").write_text(f'source = "{source}"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    return own


@pytest.mark.parametrize("mode", ["--linked", "--vendored"])
def test_add_names_the_project_skill_the_kit_supplies_and_keeps_it(
    bare_project: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    own = _catalog(fake_home, monkeypatch, bare_project)

    assert main(["kit", "add", "rn", mode, "--root", str(bare_project), "--yes"]) == 0

    out = capsys.readouterr().out.splitlines()
    assert [line for line in out if "already supplies" in line] == [
        identical_skill_note("rn", "probe")
    ]
    assert (own / "SKILL.md").is_file()
    assert "rm -r loadout/skills/probe" in identical_skill_note("rn", "probe")
