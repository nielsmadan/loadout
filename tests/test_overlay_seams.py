from __future__ import annotations

import inspect
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
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
    "readers": ["folder", "slice_name", "harness", "selected"],
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


@pytest.mark.integration
@pytest.mark.parametrize("module", ["loadout.unmanaged", "loadout.emit", "loadout.commands"])
def test_a_record_module_importing_the_seams_hub_leaves_every_1_0_module_importable(
    tmp_path: Path, module: str
) -> None:
    package = tmp_path / "loadout"
    shutil.copytree(Path(loadout.__file__).parent, package)
    documents = package / "overlay_documents.py"
    text = documents.read_text(encoding="utf-8")
    documents.write_text(
        text.replace(
            "from .credentials import", "from . import overlay_seams\nfrom .credentials import", 1
        ),
        encoding="utf-8",
    )
    assert "from . import overlay_seams" in documents.read_text(encoding="utf-8")
    probe = f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import {module}; print({module}.__file__)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe], check=False, capture_output=True, text=True
    )
    assert result.stdout.strip() == str(package / f"{module.split('.')[1]}.py"), result.stderr


@pytest.mark.parametrize("shown", [True, False])
def test_every_abort_sentence_names_the_command_the_overlay_rewrites(shown: bool) -> None:
    sentences = overlay_seams.abort_sentences({"hand-edited", "owned-key-removed"}, shown=shown)
    assert len(sentences) == 2
    assert all("`loadout sync --force`" in sentence for sentence in sentences)
