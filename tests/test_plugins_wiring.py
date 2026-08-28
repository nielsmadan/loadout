"""The plugins slice from manifest to rendered file.

Everything else about plugins is tested as pure functions. This is the one that
proves `loadout` actually writes the enablement into each harness's own file — a
slice nothing calls is a library, not a feature.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest

from loadout.emit import Merged, render_global
from loadout.errors import LoadoutError

FRAGMENT: dict[str, Any] = {
    "marketplaces": {"nolabs-ai": {"source_type": "local", "source": "/marketplaces/nolabs-ai"}},
    "plugins": {
        "superpowers": {
            "source": "git:github.com/obra/superpowers",
            "marketplace": "claude-plugins-official",
            "pi": {"extensions": []},
        },
        "nono": {"source": "/packages/nono", "marketplace": "nolabs-ai"},
    },
}

MANIFEST = """\
[[source]]
name = "test"
path = "."

[claude]
settings = ["claude"]
plugins  = ["kit"]

[codex]
plugins = ["kit"]

[pi]
plugins = ["kit"]
"""


def build(root: Path, fragments: dict[str, dict[str, Any]], manifest: str = MANIFEST) -> Path:
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    for name, body in fragments.items():
        (root / "plugins" / f"{name}.json").write_text(json.dumps(body), encoding="utf-8")
    (root / "settings").mkdir(exist_ok=True)
    (root / "settings" / "claude.json").write_text('{"model": "opus"}', encoding="utf-8")
    (root / "permissions.toml").write_text("[shell]\nallow = ['ls']\n", encoding="utf-8")
    (root / "loadout.toml").write_text(manifest, encoding="utf-8")
    return root


def written(outputs: dict[Path, str], name: str) -> tuple[Path, str]:
    hits = {p: c for p, c in outputs.items() if p.name == name}
    assert len(hits) == 1, f"expected one {name}, got {sorted(hits)}"
    return next(iter(hits.items()))


def claude_settings(outputs: dict[Path, str]) -> dict[str, Any]:
    hits = [c for p, c in outputs.items() if p.parts[-2:] == (".claude", "settings.json")]
    assert len(hits) == 1, "expected one Claude settings.json"
    return json.loads(hits[0])


def pi_settings(outputs: dict[Path, str]) -> dict[str, Any]:
    hits = [c for p, c in outputs.items() if p.parts[-3:] == (".pi", "agent", "settings.json")]
    assert len(hits) == 1, "expected one Pi settings.json"
    return json.loads(hits[0])


def test_one_fragment_reaches_all_three_harnesses(tmp_path: Path) -> None:
    """The same reference, addressed three ways: by name and marketplace on
    Claude and Codex, by source on Pi."""
    outputs = render_global(build(tmp_path, {"kit": FRAGMENT}), "default")

    assert claude_settings(outputs)["enabledPlugins"] == {
        "superpowers@claude-plugins-official": True,
        "nono@nolabs-ai": True,
    }
    assert pi_settings(outputs)["packages"] == [
        {"source": "git:github.com/obra/superpowers", "extensions": []},
        "/packages/nono",
    ]
    _, codex = written(outputs, "config.toml")
    assert isinstance(codex, Merged)
    assert tomllib.loads(codex.document)["plugins"] == {
        "superpowers@claude-plugins-official": {"enabled": True},
        "nono@nolabs-ai": {"enabled": True},
    }


def test_claude_plugins_compose_with_permissions_and_the_settings_residual(
    tmp_path: Path,
) -> None:
    """`enabledPlugins` is the fourth slice landing in settings.json, so it has
    to arrive without displacing the three already there."""
    document = claude_settings(render_global(build(tmp_path, {"kit": FRAGMENT}), "default"))
    assert document["model"] == "opus"
    assert document["permissions"]["allow"] == ["Bash(ls:*)"]
    assert list(document["enabledPlugins"]) == [
        "superpowers@claude-plugins-official",
        "nono@nolabs-ai",
    ]


def test_a_second_fragment_adds_a_plugin_rather_than_replacing_the_list(tmp_path: Path) -> None:
    """The slice's whole point: a profile states a delta instead of restating a
    list (spec 1 §8, maps merge key by key)."""
    extra = {"plugins": {"telegram": {"marketplace": "claude-plugins-official"}}}
    root = build(tmp_path, {"kit": FRAGMENT, "extra": extra})
    (root / "loadout.toml").write_text(
        MANIFEST.replace('plugins  = ["kit"]', 'plugins  = ["kit", "extra"]'), encoding="utf-8"
    )
    assert list(claude_settings(render_global(root, "default"))["enabledPlugins"]) == [
        "superpowers@claude-plugins-official",
        "nono@nolabs-ai",
        "telegram@claude-plugins-official",
    ]


def test_a_null_overlay_switches_a_plugin_off(tmp_path: Path) -> None:
    """How a profile takes a plugin out without deleting the fragment declaring
    it — the 208-line-copy problem, in the shape plugins takes."""
    off = {"plugins": {"nono": None}}
    root = build(tmp_path, {"kit": FRAGMENT, "off": off})
    (root / "loadout.toml").write_text(
        MANIFEST.replace('plugins  = ["kit"]', 'plugins  = ["kit", "off"]'), encoding="utf-8"
    )
    assert list(claude_settings(render_global(root, "default"))["enabledPlugins"]) == [
        "superpowers@claude-plugins-official"
    ]


def test_an_empty_plugins_list_renders_an_empty_enablement(tmp_path: Path) -> None:
    """A content renderer's off switch is an empty fragment list, not `rules = []`."""
    root = build(
        tmp_path, {"kit": FRAGMENT}, MANIFEST.replace('plugins  = ["kit"]', "plugins = []")
    )
    assert claude_settings(render_global(root, "default"))["enabledPlugins"] == {}


def test_the_destinations_follow_each_harnesss_config_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0011 — a relocated harness is followed without editing a manifest."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "moved-claude"))
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "moved-pi"))
    outputs = render_global(build(tmp_path, {"kit": FRAGMENT}), "default")
    paths = {p for p in outputs if p.name == "settings.json"}
    assert paths == {
        tmp_path / "moved-claude" / "settings.json",
        tmp_path / "moved-pi" / "settings.json",
    }


def test_opencode_rejects_a_plugins_key(tmp_path: Path) -> None:
    """OpenCode has no enablement list — a plugin is on because its file exists —
    so naming the slice is an error rather than a silently ignored key."""
    root = build(
        tmp_path,
        {"kit": FRAGMENT},
        "[[source]]\nname='t'\npath='.'\n\n[opencode]\nplugins = ['kit']\n",
    )
    with pytest.raises(LoadoutError, match="unknown slice"):
        render_global(root, "default")


def test_an_agent_block_naming_no_plugins_renders_none(tmp_path: Path) -> None:
    """Unlike permissions and mcp, plugins is not automatic: which plugins are on
    is an authoring decision, and an absent key means 'loadout does not manage
    this' rather than 'none'."""
    root = build(tmp_path, {"kit": FRAGMENT}, "[[source]]\nname='t'\npath='.'\n\n[claude]\n")
    assert "enabledPlugins" not in claude_settings(render_global(root, "default"))
