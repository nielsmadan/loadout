from __future__ import annotations

import json
from pathlib import Path

from loadout.agents import GLOBAL_PRESET
from loadout.emit import Merged, render_global, render_project
from loadout.templates import vendored_path


def test_a_project_renders_mcp_json_for_claude(project: Path) -> None:
    (project / "loadout" / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://mcp.jina.ai/v1"\n', encoding="utf-8"
    )

    rendered = render_project(project)

    document = json.loads(rendered[project / ".mcp.json"])
    assert list(document["mcpServers"]) == ["jina"]


def test_pi_gets_no_project_destination(project: Path) -> None:
    """pi-mcp-adapter calls .mcp.json its "Preferred project config" and uses it
    immediately, so writing .pi/mcp.json too would hand Pi the same servers twice
    under two names."""
    (project / "loadout" / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://x"\n', encoding="utf-8"
    )

    rendered = render_project(project)

    assert not any(str(p).endswith(".pi/mcp.json") for p in rendered)


def test_codex_gets_no_project_destination_yet(project: Path) -> None:
    """Whether [mcp_servers.*] survives Codex's project-config filter is
    unverified — its own warning says unsupported project-local keys are ignored.
    An absent entry is honest; one written on an assumption is the failure this
    project has recorded six times. Delete this test when the probe answers."""
    (project / "loadout" / "mcp.toml").write_text(
        '[jina]\ntransport = "http"\nurl = "https://x"\n', encoding="utf-8"
    )

    rendered = render_project(project)

    assert not any(".codex" in str(p) and "config.toml" in str(p) for p in rendered)


def test_a_template_contributes_its_servers(project: Path) -> None:
    """Same tier rule as every other slice: a template sits beneath the project."""
    # the shared project fixture already declares a vendored template
    template = vendored_path(project, "web")
    (template / "mcp.toml").write_text(
        '[from-template]\ntransport = "stdio"\ncommand = "t"\n', encoding="utf-8"
    )

    rendered = render_project(project)

    document = json.loads(rendered[project / ".mcp.json"])
    assert "from-template" in document["mcpServers"]


# --------------------------------------------------------------------------
# Global scope
# --------------------------------------------------------------------------

JINA = '[jina]\ntransport = "http"\nurl = "https://mcp.jina.ai/v1"\n'


def build_global(tmp_path: Path, agent_block: str, mcp_toml: str | None = JINA) -> Path:
    """A minimal global root that goes through agent blocks — `[claude]` etc. —
    rather than the shared `root` fixture, which uses only the hand-written
    `[permissions.*]` spelling and so never reaches the preset's automatic
    slices at all."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n' + agent_block, encoding="utf-8"
    )
    (tmp_path / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    if mcp_toml is not None:
        (tmp_path / "mcp.toml").write_text(mcp_toml, encoding="utf-8")
    return tmp_path


def test_claude_global_is_staged_rather_than_written() -> None:
    """${CLAUDE_CONFIG_DIR}/.claude.json is runtime state — history, project
    entries, caches — and settings.json has no mcpServers key, so there is no
    file to write. loadout renders a staged document and stops; something else
    feeds it to `claude mcp add-json`. Render and invoke stay separate (ADR 0004).
    """
    entry = GLOBAL_PRESET["claude"]["mcp"]

    assert entry.output is not None
    assert entry.destination is None


def test_no_mcp_toml_means_no_servers_output(tmp_path: Path) -> None:
    """`mcp` is automatic, but a source offering none contributes no tier —
    mirroring project scope's identical guard rather than writing an empty
    document nobody asked for."""
    root = build_global(tmp_path, "\n[claude]\n", mcp_toml=None)

    rendered = render_global(root)

    assert not any("mcp-servers.generated.json" in str(p) for p in rendered)


def test_a_global_source_renders_claude_servers_without_being_named(tmp_path: Path) -> None:
    """`mcp` is automatic — defining a server needs no per-agent authoring
    decision, the same reasoning that makes `permissions` automatic."""
    root = build_global(tmp_path, "\n[claude]\n")

    rendered = render_global(root)

    staged = next(p for p in rendered if str(p).endswith("mcp-servers.generated.json"))
    document = json.loads(rendered[staged])
    assert document == {"jina": {"type": "http", "url": "https://mcp.jina.ai/v1"}}


def test_codex_global_writes_config_toml(tmp_path: Path, fake_home: Path) -> None:
    """No longer staged: declared ownership lets loadout write the real file, so
    `[mcp_servers.…]` reaches Codex without a merge script the user provides."""
    root = build_global(tmp_path, "\n[codex]\n")

    rendered = render_global(root)

    merged = rendered[fake_home / ".codex" / "config.toml"]
    assert isinstance(merged, Merged)
    assert merged.owned == frozenset({"mcp_servers"})
    assert "[mcp_servers.jina]" in merged.document
    assert root / "codex" / "config.toml" not in rendered


def test_pi_global_writes_its_own_mcp_json(tmp_path: Path) -> None:
    root = build_global(tmp_path, "\n[pi]\npermissions = false\n")

    rendered = render_global(root)

    written = next(p for p in rendered if str(p).endswith("/.pi/agent/mcp.json"))
    document = json.loads(rendered[written])
    assert document == {"mcpServers": {"jina": {"url": "https://mcp.jina.ai/v1"}}}


def test_opencode_global_composes_the_mcp_key_with_permission(tmp_path: Path) -> None:
    """opencode-servers is a ValueSpec sharing opencode.json with the
    permissions slice — the same composing shape project scope already proves,
    now at global scope."""
    root = build_global(tmp_path, "\n[opencode]\n")

    rendered = render_global(root)

    written = next(p for p in rendered if str(p).endswith("opencode/opencode.json"))
    document = json.loads(rendered[written])
    assert document["mcp"] == {"jina": {"type": "remote", "url": "https://mcp.jina.ai/v1"}}
    assert "permission" in document
