from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from loadout.emit import Merged, compose_permission_document
from loadout.errors import LoadoutError
from loadout.manifest import PermissionTarget
from loadout.permissions import renderers as registry
from loadout.permissions.renderers import MergedTomlSpec
from loadout.permissions.rules import EMPTY_RULES

DESTINATION = Path("/nowhere/config.toml")


def _contributor(
    name: str, renderer: str
) -> tuple[PermissionTarget, dict[str, Any], dict[str, Any]]:
    target = PermissionTarget(name=name, path=PurePosixPath(f"{name}.toml"), renderer=renderer)
    return (target, {}, {})


@pytest.fixture(autouse=True)
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, document, owns in (
        (
            "t-servers",
            '[mcp_servers.jina]\ndefault_tools_approval_mode = "approve"\n',
            "mcp_servers",
        ),
        ("t-model", 'model = "gpt-5.6-sol"\n', "model"),
        ("t-clash", "[mcp_servers.other]\nx = 1\n", "mcp_servers"),
    ):
        monkeypatch.setitem(
            registry.RENDERERS,
            name,
            MergedTomlSpec(fn=lambda _rules, _content, text=document: text, owns=frozenset({owns})),
        )


def test_two_slices_become_one_application() -> None:
    """Applying each separately would mean the second read the first's result —
    ADR 0001's feedback, one layer down — so the whole point is that three slices
    writing config.toml produce a single Merged that `write_all` applies once."""
    result = compose_permission_document(
        [_contributor("servers", "t-servers"), _contributor("model", "t-model")],
        EMPTY_RULES,
        DESTINATION,
    )

    assert isinstance(result, Merged)
    assert result.owned == frozenset({"mcp_servers", "model"})
    assert "[mcp_servers.jina]" in result.document
    assert 'model = "gpt-5.6-sol"' in result.document


def test_a_later_slices_scalar_stays_above_an_earlier_slices_table() -> None:
    """TOML reads a bare key after `[table]` as a member of it, so concatenating
    fragments in contributor order would silently reassign `model` into
    `mcp_servers.jina` — spelled right, configuring something else."""
    result = compose_permission_document(
        [_contributor("servers", "t-servers"), _contributor("model", "t-model")],
        EMPTY_RULES,
        DESTINATION,
    )

    assert isinstance(result, Merged)
    lines = result.document.splitlines()
    assert lines.index('model = "gpt-5.6-sol"') < lines.index("[mcp_servers.jina]")


def test_two_slices_declaring_one_key_is_refused() -> None:
    """Both would be stripped and both rewritten, so whichever ran last would
    silently drop the other's servers."""
    with pytest.raises(LoadoutError, match=r"\['mcp_servers'\] is already owned"):
        compose_permission_document(
            [_contributor("servers", "t-servers"), _contributor("clash", "t-clash")],
            EMPTY_RULES,
            DESTINATION,
        )


def test_a_merged_slice_cannot_share_a_file_with_one_that_rewrites_it_whole() -> None:
    """`codex-servers` builds its document from scratch; merging into the same file
    would have it discard whatever the merged slice wrote."""
    with pytest.raises(LoadoutError, match="cannot compose with servers-text"):
        compose_permission_document(
            [_contributor("servers", "t-servers"), _contributor("servers-text", "codex-servers")],
            EMPTY_RULES,
            DESTINATION,
        )
