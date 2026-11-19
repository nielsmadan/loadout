from __future__ import annotations

import ast
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout import stealth_seams

PACKAGE = Path(loadout.__file__).parent
LEAVES = (
    "exclude",
    "stealth_git",
    "stealth_lock",
    "stealth_preset",
    "stealth_registry",
    "stealth_targets",
)

SIGNATURES = {
    "read_toml": ["path"],
    "build_manifest": ["data", "path", "config_paths", "sources"],
    "claim": ["path", "owner", "claimed"],
    "serialize_json": ["document", "ensure_ascii"],
    "opencode_mcp_keys": ["rules"],
    "codex_rules": ["document", "label"],
    "abort_sentences": ["kinds", "shown"],
    "write_full_diff": ["lines"],
    "readers": ["folder", "slice_name", "harness", "selected"],
}


@pytest.mark.parametrize(("name", "parameters"), SIGNATURES.items())
def test_a_seam_keeps_the_signature_the_stealth_source_calls(
    name: str, parameters: list[str]
) -> None:
    assert list(inspect.signature(getattr(stealth_seams, name)).parameters) == parameters


def test_the_seams_module_exports_exactly_the_pinned_names() -> None:
    exported = {
        name
        for name, value in vars(stealth_seams).items()
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
    documents = package / "stealth_documents.py"
    text = documents.read_text(encoding="utf-8")
    documents.write_text(
        text.replace(
            "from .credentials import", "from . import stealth_seams\nfrom .credentials import", 1
        ),
        encoding="utf-8",
    )
    assert "from . import stealth_seams" in documents.read_text(encoding="utf-8")
    probe = f"import sys; sys.path.insert(0, {str(tmp_path)!r}); import {module}; print({module}.__file__)"
    result = subprocess.run(
        [sys.executable, "-I", "-c", probe], check=False, capture_output=True, text=True
    )
    assert result.stdout.strip() == str(package / f"{module.split('.')[1]}.py"), result.stderr


@pytest.mark.parametrize("shown", [True, False])
def test_every_abort_sentence_names_the_command_the_stealth_source_rewrites(shown: bool) -> None:
    sentences = stealth_seams.abort_sentences({"hand-edited", "owned-key-removed"}, shown=shown)
    assert len(sentences) == 2
    assert all("`loadout sync --force`" in sentence for sentence in sentences)


def _source(module: str) -> Path:
    base = PACKAGE.joinpath(*module.split("."))
    return base / "__init__.py" if base.is_dir() else base.with_suffix(".py")


def _top_imports(module: str) -> set[str]:
    path = _source(module)
    parts = module.split(".")
    package = parts if path.name == "__init__.py" else parts[:-1]
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        parent = package[: len(package) - node.level + 1]
        base = [*parent, node.module] if node.module else parent
        found.add(".".join(base))
        found.update(".".join([*base, alias.name]) for alias in node.names)
    return {name for name in found if name and _source(name).exists()}


def _reached(module: str) -> set[str]:
    seen: set[str] = set()
    todo = [module]
    while todo:
        current = todo.pop()
        if current not in seen:
            seen.add(current)
            todo.extend(_top_imports(current))
    return seen - {module}


ABOVE_COMMANDS = ("cli", "kit_fanout")


def _modules() -> list[str]:
    names = (
        ".".join(path.relative_to(PACKAGE).with_suffix("").parts)
        for path in sorted(PACKAGE.rglob("*.py"))
        if path.name != "__init__.py"
    )
    return [
        name
        for name in names
        if not name.split(".")[-1].startswith(("stealth_", "_")) and name not in ABOVE_COMMANDS
    ]


def _stealth_reached(module: str) -> set[str]:
    return {name for name in _reached(module) if name.startswith("stealth_") or name == "exclude"}


@pytest.mark.parametrize("module", _modules())
def test_a_1_0_module_reaches_only_the_stealth_leaves_at_its_top(module: str) -> None:
    assert "stealth_lock" in _reached("stealth_registry")
    assert _stealth_reached(module) <= set(LEAVES)


@pytest.mark.parametrize("module", ABOVE_COMMANDS)
def test_a_module_above_commands_reaches_the_stealth_sync_at_its_top(module: str) -> None:
    assert "stealth_sync" in _stealth_reached(module)


def test_personal_does_not_reach_emit_at_its_top() -> None:
    assert "stealth_targets" in _reached("personal")
    assert "emit" not in _reached("personal")
