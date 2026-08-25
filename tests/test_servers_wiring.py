from __future__ import annotations

import json
from pathlib import Path

from loadout.emit import render_project
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
