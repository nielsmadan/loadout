"""The inverses of the two registered mcp server-definition renderers.

Only `claude-project-servers` and `opencode-servers` are wired into `RENDERERS`
today — the global-scope renderers (`render_claude_servers`, `render_codex_servers`,
`render_pi_servers`) exist in `servers.py` but are not yet registered, so they
have no inverse to write here either.

Two properties, per `docs/reference/extraction.md`:

1. `extract(render(x)) == carried(x)`. Here `carried` is **identity**: a server
   definition is not translated the way a plugin reference is, so nothing about
   a valid document is lost — only the auth-variable interpolation syntax and
   the stdio shape (separate `command`/`args` vs. one combined array) differ per
   harness, and each is undone exactly.
2. `render(extract(document)) == document`, wherever `notes` is empty.
"""

from __future__ import annotations

import pytest

from loadout.errors import LoadoutError
from loadout.extract import EXTRACTORS, VALUE_EXTRACTORS, extract_value
from loadout.permissions.renderers import RENDERERS
from loadout.servers import Server, render_claude_project_servers, render_opencode_servers
from test_extract_roundtrip import NOT_INVERTED

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
STDIO_WITH_ENV = {
    "svc": Server(name="svc", transport="stdio", command="c", args=("x",), env={"K": "v"})
}


# --- registry ------------------------------------------------------------


def test_every_definition_renderer_has_an_inverse() -> None:
    named = set(EXTRACTORS) | set(VALUE_EXTRACTORS) | NOT_INVERTED
    for name in RENDERERS:
        if name.endswith("-servers"):
            assert name in named, name


def test_an_unknown_name_is_an_error_rather_than_a_silent_empty() -> None:
    with pytest.raises(LoadoutError, match="no value extractor"):
        extract_value("pi-servers", {})


# --- claude-project-servers ------------------------------------------------


def test_claude_http_server_round_trips() -> None:
    document = render_claude_project_servers(HTTP)

    extraction = extract_value("claude-project-servers", document)

    assert extraction.notes == ()
    assert extraction.value == HTTP
    assert render_claude_project_servers(extraction.value) == document


def test_claude_stdio_server_round_trips() -> None:
    document = render_claude_project_servers(STDIO_WITH_ENV)

    extraction = extract_value("claude-project-servers", document)

    assert extraction.notes == ()
    assert extraction.value == STDIO_WITH_ENV
    assert render_claude_project_servers(extraction.value) == document


def test_claude_http_server_without_auth_round_trips() -> None:
    unauthed = {"x": Server(name="x", transport="http", url="https://x")}
    document = render_claude_project_servers(unauthed)

    extraction = extract_value("claude-project-servers", document)

    assert extraction.value == unauthed


def test_a_bad_bearer_header_is_reported_not_guessed() -> None:
    document = {
        "mcpServers": {
            "x": {"type": "http", "url": "https://x", "headers": {"Authorization": "Basic abc"}}
        }
    }

    extraction = extract_value("claude-project-servers", document)

    assert extraction.value == {"x": Server(name="x", transport="http", url="https://x")}
    assert extraction.notes != ()


def test_an_unowned_key_in_the_mcp_json_file_is_reported() -> None:
    """`.mcp.json` has no other owner, so a stray top-level key is reported
    rather than kept — there is no base for it to belong to."""
    document = {"mcpServers": {}, "extra": True}

    extraction = extract_value("claude-project-servers", document)

    assert [n.detail for n in extraction.notes] == [
        "claude-servers: .mcp.json holds unowned key(s): extra"
    ]


def test_an_unrecognised_server_type_is_reported_and_dropped() -> None:
    document = {"mcpServers": {"x": {"type": "carrier-pigeon"}}}

    extraction = extract_value("claude-project-servers", document)

    assert extraction.value == {}
    assert extraction.notes[0].kind == "unrecognised"


def test_extraction_does_not_alias_the_document() -> None:
    document = render_claude_project_servers(STDIO_WITH_ENV)
    extracted = extract_value("claude-project-servers", document).value
    extracted["svc"].env["K"] = "mutated"
    assert document["mcpServers"]["svc"]["env"]["K"] == "v"


# --- opencode-servers --------------------------------------------------


def test_opencode_http_server_round_trips() -> None:
    document = {"mcp": render_opencode_servers(HTTP), "permission": {}}

    extraction = extract_value("opencode-servers", document)

    assert extraction.notes == ()
    assert extraction.value == HTTP
    assert render_opencode_servers(extraction.value) == document["mcp"]


def test_opencode_stdio_server_round_trips() -> None:
    document = {"mcp": render_opencode_servers(STDIO), "permission": {}}

    extraction = extract_value("opencode-servers", document)

    assert extraction.notes == ()
    assert extraction.value == STDIO
    assert render_opencode_servers(extraction.value) == document["mcp"]


def test_opencode_does_not_note_the_rest_of_the_document() -> None:
    """A value renderer's inverse is handed no residual: `permission` and
    everything else in opencode.json belongs to another slice's extractor."""
    document = {"mcp": render_opencode_servers(HTTP), "permission": {"bash": {}}, "other": 1}

    extraction = extract_value("opencode-servers", document)

    assert extraction.notes == ()


def test_a_missing_mcp_key_extracts_empty_rather_than_failing() -> None:
    assert extract_value("opencode-servers", {"permission": {}}).value == {}


def test_an_unrecognised_opencode_server_type_is_reported() -> None:
    extraction = extract_value("opencode-servers", {"mcp": {"x": {"type": "carrier-pigeon"}}})
    assert extraction.value == {}
    assert extraction.notes[0].kind == "unrecognised"


def test_opencode_extraction_does_not_alias_the_document() -> None:
    document = {"mcp": render_opencode_servers(STDIO_WITH_ENV), "permission": {}}
    extracted = extract_value("opencode-servers", document).value
    extracted["svc"].env["K"] = "mutated"
    assert document["mcp"]["svc"]["environment"]["K"] == "v"
