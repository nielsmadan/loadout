from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from fixture_root import build_root
from loadout.agents import GLOBAL_PRESET, SliceOutput
from loadout.emit import Merged, render_global, write_all
from loadout.errors import LoadoutError
from loadout.permissions.renderers import RENDERERS, JsonSpec, ValueSpec

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


def test_claude_mcp_writes_a_destination_rather_than_staging(tmp_path: Path) -> None:
    """It staged into the repo until 2026-09-04; now it owns one key of a file the
    harness also writes, so nothing in the repo is produced for it."""
    root = build(tmp_path, "\n[claude]\n")
    # The slice renders nothing without a server to render.
    (root / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://jina.example"\n', encoding="utf-8"
    )
    paths = rendered(root)
    assert not any(p.endswith("mcp-servers.generated.json") for p in paths)
    assert any(p.endswith("/.claude.json") for p in paths)


def test_an_unknown_agent_is_rejected_with_the_known_ones(tmp_path: Path) -> None:
    root = build(tmp_path, "\n[claud]\n")
    with pytest.raises(LoadoutError, match="unknown agent"):
        render_global(root)


def test_an_unknown_slice_names_what_the_agent_offers(tmp_path: Path) -> None:
    # `plugins`, not `hooks` or `mcp` — OpenCode gained a hooks slice with the
    # adapters and a global `mcp` slice with server definitions; it still has
    # no plugins slice (see the comment beneath GLOBAL_PRESET in agents.py).
    root = build(tmp_path, "\n[opencode]\nplugins = []\n")
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


def test_a_contributor_writes_one_key_and_the_residual_survives(tmp_path: Path) -> None:
    """The shape Claude's hooks needs: settings supplies the whole file, a
    contributor supplies one key's value, and neither loses to the other.

    Before the split, whichever slice sorted first supplied the document — so a
    contributor running first put its content at top level and the settings
    fragment vanished entirely.
    """
    real = GLOBAL_PRESET["opencode"]["hooks"]
    GLOBAL_PRESET["opencode"]["hooks"] = SliceOutput(
        renderer="hooks-test",
        destination=GLOBAL_PRESET["opencode"]["permissions"].destination,
        source_slice="hooks",
        owned_key="hooks",
    )
    RENDERERS["hooks-test"] = ValueSpec(lambda content: content)
    try:
        root = build(tmp_path, '\n[opencode]\nsettings = "opencode"\nhooks = ["a"]\n')
        (root / "settings").mkdir(exist_ok=True)
        (root / "settings" / "opencode.json").write_text('{"model": "kept"}\n', encoding="utf-8")
        (root / "hooks").mkdir(exist_ok=True)
        (root / "hooks" / "a.json").write_text('{"hello": "world"}\n', encoding="utf-8")

        doc = json.loads(next(t for p, t in rendered(root).items() if p.endswith("opencode.json")))
        assert doc["hooks"] == {"hello": "world"}, "contributor value under its owned key"
        assert doc["model"] == "kept", "the settings residual must reach the file"
        assert "alpha" in json.dumps(doc["permission"]), "permissions still rendered"
        assert "hello" not in doc, "contributor content must not leak to top level"
    finally:
        # Restore, never delete. OpenCode has a real hooks slice now, and a
        # `del` here removed it for every test that ran afterwards — passing
        # alone and failing in the suite.
        GLOBAL_PRESET["opencode"]["hooks"] = real
        del RENDERERS["hooks-test"]


def test_a_value_renderer_without_an_owned_key_is_rejected(tmp_path: Path) -> None:
    """The two renderer kinds are distinguishable at registration, so a mismatch
    fails rather than quietly writing the wrong shape."""
    real = GLOBAL_PRESET["opencode"]["hooks"]
    GLOBAL_PRESET["opencode"]["hooks"] = SliceOutput(
        renderer="hooks-test",
        destination=GLOBAL_PRESET["opencode"]["permissions"].destination,
        source_slice="hooks",
    )
    RENDERERS["hooks-test"] = ValueSpec(lambda content: content)
    try:
        root = build(tmp_path, "\n[opencode]\nhooks = []\n")
        with pytest.raises(LoadoutError, match="names no owned_key"):
            render_global(root)
    finally:
        GLOBAL_PRESET["opencode"]["hooks"] = real
        del RENDERERS["hooks-test"]


def test_false_switches_an_automatic_slice_off(tmp_path: Path) -> None:
    """The only way to say "not this one". An absent key means *automatic*, and
    `[]` means "render with no rules selected" — which still writes a file.

    Needed when a harness stops reading what loadout writes: the renderer stays a
    capability for whoever does use it, and one machine's manifest says it has no
    use for it.
    """
    root = build_root(tmp_path)
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text() + "\n[pi]\npermissions = false\n", encoding="utf-8")

    rendered = render_global(root)

    assert not any("pi-permission-system" in str(p) for p in rendered)


def test_an_automatic_slice_renders_when_not_switched_off(tmp_path: Path) -> None:
    """The loser of the pair above — without it, a guard that never rendered pi
    at all would pass the first test."""
    root = build_root(tmp_path)
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text() + "\n[pi]\n", encoding="utf-8")

    rendered = render_global(root)

    assert any("pi-permission-system" in str(p) for p in rendered)


REMOVE_FRAGMENT = '{"model": "gpt-6", "$remove": ["developer_instructions"]}'


def test_a_removed_key_is_owned_but_rendered_nowhere(tmp_path: Path) -> None:
    """`$remove` exists because a fragment otherwise says "own this key and give it
    this value", and there is no value that means "write nothing". `null` is taken —
    merge_documents reads it as *drop from the fragment*, which un-owns the key
    instead of evicting it."""
    root = build(tmp_path, '[codex]\ndefaults = "d"\n')
    (root / "defaults").mkdir(exist_ok=True)
    (root / "defaults" / "d.json").write_text(REMOVE_FRAGMENT, encoding="utf-8")

    merged = next(v for k, v in render_global(root).items() if str(k).endswith("config.toml"))

    assert isinstance(merged, Merged)
    assert "developer_instructions" in merged.owned
    assert "developer_instructions" not in merged.document
    assert "model" in merged.document


def test_a_removed_key_is_stripped_from_the_destination(tmp_path: Path, fake_home: Path) -> None:
    """End to end: a key another tool keeps writing back is gone after a sync, body
    and all, while everything around it survives."""
    root = build(tmp_path, '[codex]\ndefaults = "d"\n')
    (root / "defaults").mkdir(exist_ok=True)
    (root / "defaults" / "d.json").write_text(REMOVE_FRAGMENT, encoding="utf-8")
    config = fake_home / ".codex" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    q = '"' * 3
    config.write_text(
        f"developer_instructions = {q}\nsomething else wrote this\n{q}\n\n"
        f'[projects."/work"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )

    write_all(root)

    written = config.read_text()
    assert "something else wrote this" not in written
    assert tomllib.loads(written)["projects"]["/work"]["trust_level"] == "trusted"
    assert tomllib.loads(written)["model"] == "gpt-6"


def test_a_key_cannot_be_both_valued_and_removed(tmp_path: Path) -> None:
    """Contradictory rather than merely redundant: one says write this, the other
    says write nothing, and silently preferring either would surprise someone."""
    root = build(tmp_path, '[codex]\ndefaults = "d"\n')
    (root / "defaults").mkdir(exist_ok=True)
    (root / "defaults" / "d.json").write_text(
        '{"model": "gpt-6", "$remove": ["model"]}', encoding="utf-8"
    )

    with pytest.raises(LoadoutError, match="both given a value"):
        render_global(root)


def test_remove_must_be_a_list_of_names(tmp_path: Path) -> None:
    root = build(tmp_path, '[codex]\ndefaults = "d"\n')
    (root / "defaults").mkdir(exist_ok=True)
    (root / "defaults" / "d.json").write_text('{"$remove": "developer_instructions"}', "utf-8")

    with pytest.raises(LoadoutError, match=r"\$remove must be a list"):
        render_global(root)


def test_the_owned_record_names_destination_keys_not_vocabulary(tmp_path: Path) -> None:
    """`$remove` is how the fragment speaks, not a key in config.toml. Recording it
    verbatim — which the record did until the union used the raw fragment keys —
    would have loadout stripping a key literally named `$remove` from the harness's
    file, and would leave the removed key itself unrecorded."""
    root = build(tmp_path, '[codex]\ndefaults = "d"\n')
    (root / "defaults").mkdir(exist_ok=True)
    (root / "defaults" / "d.json").write_text(REMOVE_FRAGMENT, encoding="utf-8")

    merged = next(v for k, v in render_global(root).items() if str(k).endswith("config.toml"))
    assert isinstance(merged, Merged)
    record = next(text for path, text in merged.records if str(path).endswith(".owned"))

    assert "$remove" not in record
    assert "developer_instructions" in record
    assert "model" in record
