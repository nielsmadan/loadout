"""Parsing mcp.toml — MCP server definitions.

Distinct from the mcp-permissions slice, which renders tool-approval policy.
This module never touches Rules.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.servers import (
    Server,
    parse_servers,
    render_claude_servers,
    render_codex_servers,
    render_opencode_servers,
    render_pi_servers,
)


def test_http_and_stdio_servers_parse(tmp_path: Path) -> None:
    source = tmp_path / "mcp.toml"
    source.write_text(
        '[jina]\ntransport = "http"\nurl = "https://mcp.jina.ai/v1"\n'
        'auth_env_var = "JINA_API_KEY"\n\n'
        '[context7]\ntransport = "stdio"\ncommand = "npx"\n'
        'args = ["-y", "@upstash/context7-mcp"]\n',
        encoding="utf-8",
    )

    servers = parse_servers(source)

    assert list(servers) == ["jina", "context7"]
    assert servers["jina"].url == "https://mcp.jina.ai/v1"
    assert servers["jina"].auth_env_var == "JINA_API_KEY"
    assert servers["context7"].args == ("-y", "@upstash/context7-mcp")


def test_declaration_order_is_preserved(tmp_path: Path) -> None:
    """Emission order follows the source, as it does for every other slice."""
    source = tmp_path / "mcp.toml"
    source.write_text(
        '[zulu]\ntransport = "stdio"\ncommand = "z"\n\n'
        '[alpha]\ntransport = "stdio"\ncommand = "a"\n',
        encoding="utf-8",
    )

    assert list(parse_servers(source)) == ["zulu", "alpha"]


def test_an_unknown_transport_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "mcp.toml"
    source.write_text('[x]\ntransport = "carrier-pigeon"\n', encoding="utf-8")

    with pytest.raises(LoadoutError, match="unknown transport"):
        parse_servers(source)


def test_http_without_a_url_is_refused(tmp_path: Path) -> None:
    """Fail at parse, not at render: four renderers would each fail differently."""
    source = tmp_path / "mcp.toml"
    source.write_text('[x]\ntransport = "http"\n', encoding="utf-8")

    with pytest.raises(LoadoutError, match="url"):
        parse_servers(source)


def test_stdio_without_a_command_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "mcp.toml"
    source.write_text('[x]\ntransport = "stdio"\n', encoding="utf-8")

    with pytest.raises(LoadoutError, match="command"):
        parse_servers(source)


def test_a_missing_file_is_no_servers_rather_than_an_error(tmp_path: Path) -> None:
    """A source offering no mcp.toml contributes nothing, the same way a template
    offering no permissions.toml contributes no tier."""
    assert parse_servers(tmp_path / "absent.toml") == {}


def test_an_unknown_key_is_refused(tmp_path: Path) -> None:
    """Silently ignoring one is shaped like a security bug: `auth_env_varr`
    parses, yields no auth variable, and renders a server that connects
    unauthenticated with nothing saying so."""
    source = tmp_path / "mcp.toml"
    source.write_text(
        '[jina]\ntransport = "http"\nurl = "https://x"\nauth_env_varr = "K"\n',
        encoding="utf-8",
    )

    with pytest.raises(LoadoutError, match="auth_env_varr"):
        parse_servers(source)


def test_every_documented_key_is_accepted(tmp_path: Path) -> None:
    """The loser of the pair above — a guard that rejected everything would pass
    the first test."""
    source = tmp_path / "mcp.toml"
    source.write_text(
        '[a]\ntransport = "http"\nurl = "https://x"\nauth_env_var = "K"\n\n'
        '[b]\ntransport = "stdio"\ncommand = "c"\nargs = ["x"]\n\n[b.env]\nK = "v"\n',
        encoding="utf-8",
    )

    servers = parse_servers(source)

    assert servers["a"].auth_env_var == "K"
    assert servers["b"].env == {"K": "v"}


HTTP = {
    "jina": Server(
        name="jina", transport="http", url="https://mcp.jina.ai/v1", auth_env_var="JINA_API_KEY"
    )
}
STDIO = {
    "context7": Server(
        name="context7", transport="stdio", command="npx", args=("-y", "@upstash/context7-mcp")
    )
}


def test_pi_names_the_auth_variable_bearer_token_env() -> None:
    """Each harness spells the same concept differently and the spellings are
    reproduced, not harmonised (ADR 0006)."""
    doc = render_pi_servers(HTTP)
    assert doc["mcpServers"]["jina"] == {
        "url": "https://mcp.jina.ai/v1",
        "bearerTokenEnv": "JINA_API_KEY",
    }


def test_a_stdio_server_carries_command_and_args() -> None:
    doc = render_pi_servers(STDIO)
    assert doc["mcpServers"]["context7"] == {
        "command": "npx",
        "args": ["-y", "@upstash/context7-mcp"],
    }


def test_no_renderer_emits_a_secret_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """auth_env_var names a variable; the value must never reach a rendered file
    (ADR 0008). Set the variable to prove the renderers ignore it."""
    monkeypatch.setenv("JINA_API_KEY", "sk-secret-do-not-render")
    rendered = [
        json.dumps(render_claude_servers(HTTP)),
        render_codex_servers(HTTP),
        json.dumps(render_opencode_servers(HTTP)),
        json.dumps(render_pi_servers(HTTP)),
    ]
    for text in rendered:
        assert "sk-secret-do-not-render" not in text
        assert "JINA_API_KEY" in text


def test_codex_emits_a_table_per_server() -> None:
    out = render_codex_servers(STDIO)
    assert "[mcp_servers.context7]" in out
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_claude_stdio_entry_always_carries_env_even_when_empty() -> None:
    """Byte-identity with the oracle depends on this: codex omits an empty env
    table, claude does not."""
    doc = render_claude_servers(STDIO)
    assert doc["context7"]["env"] == {}


def test_codex_omits_the_env_table_when_empty() -> None:
    out = render_codex_servers(STDIO)
    assert "env" not in out


def test_codex_sorts_env_keys() -> None:
    server = {
        "s": Server(name="s", transport="stdio", command="c", env={"zulu": "1", "alpha": "2"})
    }
    out = render_codex_servers(server)
    assert out.index('alpha = "2"') < out.index('zulu = "1"')


def test_opencode_stdio_command_and_args_are_one_array() -> None:
    doc = render_opencode_servers(STDIO)
    assert doc["context7"]["command"] == ["npx", "-y", "@upstash/context7-mcp"]


def test_opencode_http_auth_uses_env_interpolation() -> None:
    doc = render_opencode_servers(HTTP)
    assert doc["jina"]["headers"] == {"Authorization": "Bearer {env:JINA_API_KEY}"}
