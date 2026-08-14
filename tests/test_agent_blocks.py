from __future__ import annotations

from pathlib import Path

import pytest

from loadout.agents import GLOBAL_PRESET, SliceOutput
from loadout.emit import render_global
from loadout.errors import LoadoutError
from loadout.permissions.renderers import RENDERERS, JsonSpec

SOURCE = """
[[source]]
name = "test"
path = "."
"""

PERMISSIONS = """
[shell]
allow = ["alpha"]
deny  = ["beta"]
"""


def build(tmp_path: Path, body: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(SOURCE + body, encoding="utf-8")
    (tmp_path / "permissions.toml").write_text(PERMISSIONS, encoding="utf-8")
    fragments = tmp_path / "instructions"
    fragments.mkdir(parents=True, exist_ok=True)
    (fragments / "intro.md").write_text("intro\n", encoding="utf-8")
    return tmp_path


def rendered(root: Path) -> dict[str, str]:
    return {str(p): text for p, text in render_global(root).items()}


def test_an_agent_block_matches_the_hand_written_spelling(tmp_path: Path) -> None:
    """The preset must reproduce what a manifest spells out, byte for byte —
    otherwise converting a real manifest silently changes output."""
    by_hand = build(
        tmp_path / "hand",
        """
[permissions.pi]
destinations = ["${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/pi-permission-system/config.json"]
render       = "pi"
""",
    )
    by_agent = build(tmp_path / "agent", "\n[pi]\n")
    assert rendered(by_hand) == rendered(by_agent)


def test_an_empty_agent_block_still_renders_its_automatic_slices(tmp_path: Path) -> None:
    """`[pi]` alone is a complete declaration — permissions needs no authoring
    decision, so it does not have to be asked for."""
    root = build(tmp_path, "\n[pi]\n")
    assert any("pi-permission-system" in p for p in rendered(root))


def test_instructions_are_not_automatic_because_they_need_an_order(tmp_path: Path) -> None:
    root = build(tmp_path, "\n[pi]\n")
    assert not any(p.endswith("AGENTS.md") for p in rendered(root))


def test_naming_instructions_renders_them_in_that_order(tmp_path: Path) -> None:
    root = build(tmp_path, '\n[pi]\ninstructions = ["intro"]\n')
    written = [p for p in rendered(root) if p.endswith("AGENTS.md")]
    assert written and "intro" in rendered(root)[written[0]]


def test_an_empty_permissions_list_selects_nothing(tmp_path: Path) -> None:
    """The old `rules = []` mechanism, spelled as an empty composition."""
    root = build(tmp_path, "\n[pi]\npermissions = []\n")
    doc = next(text for p, text in rendered(root).items() if "pi-permission-system" in p)
    assert "alpha" not in doc


def test_a_staged_slice_renders_in_repo_rather_than_to_a_destination(tmp_path: Path) -> None:
    """codex.mcp has no destination — sync_config.py consumes it."""
    root = build(tmp_path, "\n[codex]\n")
    assert any(p.endswith("codex/mcp-permissions.toml") for p in rendered(root))


def test_an_unknown_agent_is_rejected_with_the_known_ones(tmp_path: Path) -> None:
    root = build(tmp_path, "\n[claud]\n")
    with pytest.raises(LoadoutError, match="unknown agent"):
        render_global(root)


def test_an_unknown_slice_names_what_the_agent_offers(tmp_path: Path) -> None:
    root = build(tmp_path, "\n[opencode]\nhooks = []\n")
    with pytest.raises(LoadoutError, match="unknown slice"):
        render_global(root)


def test_an_agent_block_coexists_with_the_older_spelling(tmp_path: Path) -> None:
    root = build(
        tmp_path,
        """
[pi]

[permissions.codex]
destinations = ["${CODEX_HOME:-~/.codex}/rules/permissions.rules"]
render       = "codex"
""",
    )
    paths = rendered(root)
    assert any("pi-permission-system" in p for p in paths)
    assert any(p.endswith("rules/permissions.rules") for p in paths)


def test_two_slices_of_one_agent_compose_into_one_file(tmp_path: Path) -> None:
    """The case §3 exists for: two slices writing one file, threading rather
    than colliding. Uses opencode because `render_opencode` writes into the
    document it is handed; `render_pi` builds from scratch and so owns its file
    outright — see the next test."""

    def render_marker(rules: object, base: dict[str, object]) -> dict[str, object]:
        out = dict(base)
        out["marker"] = "written by the second slice"
        return out

    dest = GLOBAL_PRESET["opencode"]["permissions"].destination
    GLOBAL_PRESET["opencode"]["marker"] = SliceOutput(renderer="marker-test", destination=dest)
    RENDERERS["marker-test"] = JsonSpec(render_marker)
    try:
        root = build(tmp_path, "\n[opencode]\nmarker = []\n")
        doc = next(text for p, text in rendered(root).items() if p.endswith("opencode.json"))
        assert "written by the second slice" in doc
        assert "alpha" in doc, "the first slice's output must survive the second"
    finally:
        del GLOBAL_PRESET["opencode"]["marker"]
        del RENDERERS["marker-test"]


def test_an_owned_key_beats_a_stale_one_in_the_residual(tmp_path: Path) -> None:
    """Residual-first ordering: a stale owned key left in a settings fragment
    must lose to the slice that owns it. Extraction produces exactly such a
    fragment on first run (spec 2), so this is a real case."""
    root = build(tmp_path, '\n[opencode]\nsettings = "opencode"\n')
    (root / "settings").mkdir(exist_ok=True)
    (root / "settings" / "opencode.json").write_text(
        '{"permission": {"bash": {"STALE": "allow"}}, "keep": 1}\n', encoding="utf-8"
    )
    doc = next(text for p, text in rendered(root).items() if p.endswith("opencode.json"))
    assert "STALE" not in doc, "the owned key fed back from the residual"
    assert '"keep"' in doc, "the residual's own keys must survive"


def test_two_different_agents_naming_one_file_is_still_a_collision(tmp_path: Path) -> None:

    clash = GLOBAL_PRESET["pi"]["permissions"].destination
    GLOBAL_PRESET["codex"]["permissions"] = SliceOutput(renderer="codex", destination=clash)
    try:
        root = build(tmp_path, "\n[pi]\n\n[codex]\n")
        with pytest.raises(LoadoutError, match="claimed by both"):
            render_global(root)
    finally:
        GLOBAL_PRESET["codex"]["permissions"] = SliceOutput(
            renderer="codex", destination="${CODEX_HOME:-~/.codex}/rules/permissions.rules"
        )


def test_a_whole_file_renderer_refuses_to_compose(tmp_path: Path) -> None:
    """`render_pi` builds its document from scratch, so threading through it
    would silently discard whatever the earlier slice rendered. Say so instead."""
    dest = GLOBAL_PRESET["pi"]["permissions"].destination
    GLOBAL_PRESET["pi"]["marker"] = SliceOutput(renderer="claude", destination=dest)
    try:
        root = build(tmp_path, "\n[pi]\nmarker = []\n")
        with pytest.raises(LoadoutError, match="from scratch"):
            render_global(root)
    finally:
        del GLOBAL_PRESET["pi"]["marker"]
