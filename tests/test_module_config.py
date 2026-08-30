"""The module-config slice: a module's own config, carried verbatim.

Both shapes Pi actually uses are pinned here — a flat `<pkg>.json` at the agent
root, and `extensions/<dir>/config.json` one level down — because the second is
the reason the relative path is authored rather than derived from a module name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import Copied, render_global, write_all
from loadout.errors import LoadoutError

SOURCE = """
[[source]]
name = "test"
path = "."
"""

PERMISSIONS = """
[shell]
allow = ["alpha"]
"""

# Tab-indented on purpose: a module writes its own file with its own formatter,
# and a verbatim copy is what preserves that. Rendering would make it 2-space.
STATUSLINE = '{\n\t"density": "compact",\n\t"segments": ["model", "cwd"]\n}\n'
SUBAGENT = '{\n  "maxConcurrent": 3\n}\n'


def build(tmp_path: Path, body: str, files: dict[str, str] | None = None) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(SOURCE + body, encoding="utf-8")
    (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")
    for relative, text in (files or {}).items():
        path = tmp_path / "module-config" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_both_shapes_land_at_their_authored_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flat form and the nested one are one mechanism. `extensions/subagent/`
    is named by the module, not derived — its package is `pi-subagents`."""
    agent_dir = tmp_path / "relocated"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent_dir))
    root = build(
        tmp_path / "src",
        "[pi]\n",
        {
            "pi/pi-statusline.json": STATUSLINE,
            "pi/extensions/subagent/config.json": SUBAGENT,
        },
    )

    write_all(root)
    assert (agent_dir / "pi-statusline.json").read_text(encoding="utf-8") == STATUSLINE
    assert (agent_dir / "extensions/subagent/config.json").read_text(encoding="utf-8") == SUBAGENT


def test_the_bytes_are_copied_rather_than_reserialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `Copied` output names its source, so the module's own formatting — tabs
    here — survives. Re-emitting through the JSON writer would not."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    root = build(tmp_path / "src", "[pi]\n", {"pi/pi-statusline.json": STATUSLINE})

    output = render_global(root)[tmp_path / "agent" / "pi-statusline.json"]
    assert isinstance(output, Copied)
    assert output.source.read_text(encoding="utf-8") == STATUSLINE
    assert "\t" in STATUSLINE


def test_the_directory_is_the_declaration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An agent block naming nothing still gets module config, and a source with
    no tree for that agent contributes nothing — so every existing manifest
    renders exactly as before."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    without = render_global(build(tmp_path / "a", "[pi]\n"))
    # Other pi slices legitimately write under the agent dir, so name the file.
    assert (tmp_path / "agent" / "pi-statusline.json") not in without

    with_tree = render_global(
        build(tmp_path / "b", "[pi]\n", {"pi/pi-statusline.json": STATUSLINE})
    )
    assert (tmp_path / "agent" / "pi-statusline.json") in with_tree


def test_module_config_false_switches_it_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    root = build(
        tmp_path / "src",
        "[pi]\nmodule-config = false\n",
        {"pi/pi-statusline.json": STATUSLINE},
    )
    assert (tmp_path / "agent" / "pi-statusline.json") not in render_global(root)


def test_a_path_offered_by_two_sources_is_refused(tmp_path: Path) -> None:
    """The loser must be provably present: both source names appear, so this
    cannot pass against a build where the second source is never read."""
    root = tmp_path / "src"
    root.mkdir(parents=True)
    other = tmp_path / "other"
    for base, text in ((root, STATUSLINE), (other, SUBAGENT)):
        path = base / "module-config/pi/pi-statusline.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n'
        f'[[source]]\nname = "other"\npath = "{other}"\n\n[pi]\n',
        encoding="utf-8",
    )

    with pytest.raises(LoadoutError, match=r"pi-statusline\.json") as caught:
        render_global(root)
    message = str(caught.value)
    assert "'test'" in message
    assert "'other'" in message


def test_a_path_colliding_with_a_rendered_destination_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """settings.json under the agent root is the plugins slice's destination, so
    a module-config file naming it must collide rather than race."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    root = build(
        tmp_path / "src",
        '[pi]\nplugins = ["p"]\n',
        {"pi/settings.json": '{"packages": []}\n'},
    )
    (root / "plugins").mkdir(exist_ok=True)
    (root / "plugins" / "p.json").write_text('{"plugins": {}}\n', encoding="utf-8")

    with pytest.raises(LoadoutError, match="claimed by both"):
        render_global(root)


def test_an_executable_file_keeps_its_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Module config is not always JSON — a module may read a script — and a
    mode does not survive a str, which is why the slice copies."""
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "agent"))
    root = build(tmp_path / "src", "[pi]\n", {"pi/hook.sh": "#!/bin/sh\necho hi\n"})
    (root / "module-config/pi/hook.sh").chmod(0o755)

    write_all(root)
    assert (tmp_path / "agent" / "hook.sh").stat().st_mode & 0o111
