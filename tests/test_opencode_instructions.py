"""OpenCode takes global instructions as a document at a path, like the rest.

This was recorded as the one harness that did not — as a *list of paths inside a
settings key*, needing a shape loadout has never written. That is a real OpenCode
feature (`instructions` in `opencode.json`, which takes globs and remote URLs)
but it is not this one. Upstream's rules page has both, under separate headings:

    Global — You can also have global rules in a `~/.config/opencode/AGENTS.md`
    file. This gets applied across all opencode sessions.

    Custom Instructions — You can specify custom instruction files in your
    `opencode.json` … This allows you and your team to reuse existing rules
    rather than having to duplicate them to AGENTS.md.

So the slice is an ordinary destination, and the tests below are the ordinary
ones. What makes it worth its own file is the failure it ends, in the paragraph
that follows the first quote:

    Global rules: `~/.claude/CLAUDE.md` (used if no `~/.config/opencode/AGENTS.md`
    exists)

loadout writes that fallback. So OpenCode was reading **Claude's** generated
document — Claude's fragment order, Claude-specific fragments and all — and the
symptom of the missing slice was a file full of plausible instructions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import render_global

MANIFEST = """\
[[source]]
name = "test"
path = "."

[all]
instructions = ["shared"]

[claude]
instructions = ["claude-only"]

[opencode]
"""


def build(root: Path, manifest: str = MANIFEST) -> Path:
    fragments = root / "instructions"
    fragments.mkdir(parents=True, exist_ok=True)
    (fragments / "shared.md").write_text("shared body\n", encoding="utf-8")
    (fragments / "claude-only.md").write_text("claude body\n", encoding="utf-8")
    (root / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    (root / "loadout.toml").write_text(manifest, encoding="utf-8")
    return root


def rendered(root: Path) -> dict[str, str]:
    return {str(p): t for p, t in render_global(root).items()}


def test_opencode_gets_its_own_agents_md(tmp_path: Path) -> None:
    written = rendered(build(tmp_path))
    document = next(t for p, t in written.items() if p.endswith("opencode/AGENTS.md"))
    assert "shared body" in document


def test_the_document_is_opencodes_own_not_claudes(tmp_path: Path) -> None:
    """The whole point. Before this slice, OpenCode fell through to
    `~/.claude/CLAUDE.md`, so it read fragments chosen for a different harness."""
    written = rendered(build(tmp_path))
    document = next(t for p, t in written.items() if p.endswith("opencode/AGENTS.md"))
    claude = next(t for p, t in written.items() if p.endswith("CLAUDE.md"))
    assert "claude body" in claude
    assert "shared body" in document and "claude body" not in document


def test_the_destination_follows_opencodes_config_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0011. OpenCode's is `XDG_CONFIG_HOME`, which relocates the whole
    `opencode/` directory rather than naming it — so the subdirectory stays."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "moved"))
    written = rendered(build(tmp_path))
    assert str(tmp_path / "moved" / "opencode" / "AGENTS.md") in written


def test_instructions_stay_out_of_opencode_json(tmp_path: Path) -> None:
    """A document at a path, not a key. `instructions` in opencode.json includes
    files someone else wrote; pointing it at loadout's own output would restate
    what AGENTS.md already is."""
    written = rendered(build(tmp_path))
    config = next(t for p, t in written.items() if p.endswith("opencode.json"))
    assert "instructions" not in config


def test_an_agent_block_naming_no_instructions_writes_no_document(tmp_path: Path) -> None:
    """Instructions need an order, so — unlike permissions and mcp — the slice
    renders only when a manifest asks for it."""
    written = rendered(build(tmp_path, MANIFEST.replace('instructions = ["shared"]', "")))
    assert not any(p.endswith("opencode/AGENTS.md") for p in written)
