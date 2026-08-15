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


def build(tmp_path: Path, body: str) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(SOURCE + body, encoding="utf-8")
    (tmp_path / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    f = tmp_path / "instructions"
    f.mkdir(exist_ok=True)
    for name, text in (
        ("intro", "shared intro"),
        ("policy", "interactive policy"),
        ("policy.autonomous", "autonomous policy"),
        ("claude-intro", "claude intro"),
    ):
        (f / f"{name}.md").write_text(text + "\n", encoding="utf-8")
    p = tmp_path / "plugins"
    p.mkdir(exist_ok=True)
    (p / "kit.json").write_text('{"plugins": {"demo": {"source": "npm:demo"}}}', encoding="utf-8")
    return tmp_path


def docs(root: Path, profile: str = "default") -> dict[str, str]:
    return {str(p): t for p, t in render_global(root, profile=profile).items()}


def test_all_supplies_defaults_to_every_declared_agent(tmp_path: Path) -> None:
    """Restores what `instructions.shared` did, without the pseudo-agent: each
    agent still writes its own file, from one declaration."""
    root = build(tmp_path, '\n[all]\ninstructions = ["intro"]\n\n[codex]\n\n[pi]\n')
    written = {p: t for p, t in docs(root).items() if p.endswith("AGENTS.md")}
    assert len(written) == 2, "both agents wrote their own file"
    assert all("shared intro" in t for t in written.values())


def test_an_agent_overrides_all_per_key(tmp_path: Path) -> None:
    root = build(
        tmp_path,
        '\n[all]\ninstructions = ["intro"]\n\n[claude]\ninstructions = ["claude-intro"]\n\n[pi]\n',
    )
    written = docs(root)
    claude = next(t for p, t in written.items() if p.endswith("CLAUDE.md"))
    pi = next(t for p, t in written.items() if p.endswith("AGENTS.md"))
    assert "claude intro" in claude and "shared intro" not in claude
    assert "shared intro" in pi, "the other agent keeps the default"


def test_all_does_not_enable_an_undeclared_agent(tmp_path: Path) -> None:
    """Otherwise adding [all] would silently switch on every harness."""
    root = build(tmp_path, '\n[all]\ninstructions = ["intro"]\n\n[pi]\n')
    assert not any(p.endswith("CLAUDE.md") for p in docs(root))


def test_substitute_swaps_one_fragment_without_restating_the_order(tmp_path: Path) -> None:
    """The collapse this replaces variants for: change one entry of a list
    without repeating the rest, and with nothing inferred from a filename."""
    root = build(tmp_path, '\n[pi]\ninstructions = ["intro", "policy"]\n')
    (root / "autonomous.toml").write_text(
        'extends = "default"\n\n[pi]\nsubstitute = { policy = "policy.autonomous" }\n',
        encoding="utf-8",
    )
    text = next(t for p, t in docs(root, "autonomous").items() if p.endswith("AGENTS.md"))
    assert "autonomous policy" in text
    assert "interactive policy" not in text
    assert "shared intro" in text, "unsubstituted entries are untouched"


def test_substitute_in_all_reaches_every_agent(tmp_path: Path) -> None:
    """The case that made repetition an objection to substitute: declare once."""
    root = build(tmp_path, '\n[all]\ninstructions = ["policy"]\n\n[codex]\n\n[pi]\n')
    (root / "autonomous.toml").write_text(
        'extends = "default"\n\n[all]\nsubstitute = { policy = "policy.autonomous" }\n',
        encoding="utf-8",
    )
    written = {p: t for p, t in docs(root, "autonomous").items() if p.endswith("AGENTS.md")}
    assert written and all("autonomous policy" in t for t in written.values())


def test_the_default_profile_is_unaffected_by_a_substitution(tmp_path: Path) -> None:
    root = build(tmp_path, '\n[pi]\ninstructions = ["policy"]\n')
    (root / "autonomous.toml").write_text(
        'extends = "default"\n\n[pi]\nsubstitute = { policy = "policy.autonomous" }\n',
        encoding="utf-8",
    )
    assert "interactive policy" in next(t for p, t in docs(root).items() if p.endswith("AGENTS.md"))


def test_substituting_to_a_missing_fragment_errors(tmp_path: Path) -> None:
    root = build(tmp_path, '\n[pi]\ninstructions = ["policy"]\n')
    (root / "autonomous.toml").write_text(
        'extends = "default"\n\n[pi]\nsubstitute = { policy = "nosuch" }\n', encoding="utf-8"
    )
    with pytest.raises(LoadoutError, match="nosuch"):
        render_global(root, profile="autonomous")


def test_a_default_an_agent_has_no_slice_for_is_ignored(tmp_path: Path) -> None:
    """[all] applies where it applies. OpenCode has no plugins slice — a plugin
    is on there because its file exists — so a shared plugins default is simply
    not for it, and not an error.

    This was `instructions` on OpenCode until that turned out to be a real slice
    (`~/.config/opencode/AGENTS.md`). The behaviour is unchanged; only the
    example had to be one that is still true.
    """
    root = build(tmp_path, '\n[all]\nplugins = ["kit"]\n\n[opencode]\n\n[pi]\n')
    written = docs(root)
    pi = next(t for p, t in written.items() if p.endswith("agent/settings.json"))
    assert "npm:demo" in pi, "pi still got it"
    assert any(p.endswith("opencode.json") for p in written), "opencode still rendered"


def test_a_key_the_agent_itself_names_is_still_typo_checked(tmp_path: Path) -> None:
    """The leniency is for defaults only — an agent naming a slice it has not
    got is a mistake worth reporting."""
    root = build(tmp_path, '\n[opencode]\nplugins = ["kit"]\n')
    with pytest.raises(LoadoutError, match="unknown slice"):
        render_global(root)
