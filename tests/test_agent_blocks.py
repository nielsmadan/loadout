from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global
from loadout.errors import LoadoutError

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
