from __future__ import annotations

import inspect

import pytest

from loadout import overlay_seams

SIGNATURES = {
    "read_toml": ["path"],
    "build_manifest": ["data", "path", "config_paths", "sources"],
    "claim": ["path", "owner", "claimed"],
    "serialize_json": ["document", "ensure_ascii"],
    "codex_definition_block": ["server", "block"],
    "codex_policy_block": ["tools", "block"],
    "opencode_mcp_keys": ["rules"],
    "codex_rules": ["document", "label"],
    "abort_sentences": ["kinds", "shown"],
    "write_full_diff": ["lines"],
}


@pytest.mark.parametrize(("name", "parameters"), SIGNATURES.items())
def test_a_seam_keeps_the_signature_the_overlay_calls(name: str, parameters: list[str]) -> None:
    assert list(inspect.signature(getattr(overlay_seams, name)).parameters) == parameters


def test_the_seams_module_exports_exactly_the_pinned_names() -> None:
    exported = {
        name
        for name, value in vars(overlay_seams).items()
        if not name.startswith("_") and callable(value) and not inspect.ismodule(value)
    }
    assert exported == set(SIGNATURES)
