from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .composition import render
from .errors import LoadoutError
from .manifest import Manifest, PermissionTarget, load_manifest, manifest_path
from .permissions.merge import merge_rules
from .permissions.renderers import RENDERERS, TextSpec
from .permissions.rules import EMPTY_RULES, Rules, parse_rules
from .project import load_project_config, project_config_path, project_targets
from .sources import Source

PERMISSIONS_SOURCE = ("permissions", "permissions.toml")
PROJECT_SOURCE = "permissions.toml"
PROJECT_LOCAL_SOURCE = "permissions.local.toml"


def permissions_source(manifest: Manifest) -> Source:
    offering = [
        source
        for source in manifest.sources
        if "permissions" in source.use and (source.path.joinpath(*PERMISSIONS_SOURCE)).is_file()
    ]
    if not offering:
        raise LoadoutError(
            "no source provides permissions/permissions.toml, but the manifest "
            "declares [permissions.*] targets"
        )
    if len(offering) > 1:
        names = ", ".join(sorted(s.name for s in offering))
        raise LoadoutError(
            f"more than one source provides permissions/permissions.toml ({names}); "
            f"merging permissions across sources is not implemented"
        )
    return offering[0]


def _load_base(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LoadoutError(f"base document not found: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LoadoutError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LoadoutError(f"{path}: base document must be a JSON object")
    permissions = document.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        raise LoadoutError(f"{path}: base document's permissions must be a JSON object")
    return document


def _load_existing(path: Path) -> dict[str, Any]:
    """Foreign keys in a harness's own config file, carried forward.

    Only keys loadout does not generate survive: every renderer assigns its owned
    key unconditionally, so the owned subtree is always regenerated and can never
    feed back. See ADR 0001's amendment.
    """
    if not path.is_file():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LoadoutError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(document, dict):
        raise LoadoutError(f"{path}: existing output must be a JSON object")
    return document


def _preserved(path: Path, keys: tuple[str, ...]) -> dict[str, Any]:
    if not keys or not path.is_file():
        return {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise LoadoutError(f"{path}: invalid JSON: {error}") from error
    if not isinstance(existing, dict):
        raise LoadoutError(f"{path}: existing output must be a JSON object")
    return {key: existing[key] for key in keys if key in existing}


def render_permission_target(target: PermissionTarget, rules: Rules, root: Path) -> str:
    spec = RENDERERS.get(target.renderer)
    if spec is None:
        known = ", ".join(sorted(RENDERERS))
        raise LoadoutError(
            f"permissions.{target.name}: unknown renderer {target.renderer!r} (known: {known})"
        )
    effective = rules if target.select_all else EMPTY_RULES

    if isinstance(spec, TextSpec):
        return spec.fn(effective)

    base = _load_base(root / str(target.base)) if target.base else {}
    document = spec.fn(effective, base)
    overlap = [k for k in target.preserve if k in document]
    if overlap:
        raise LoadoutError(
            f"permissions.{target.name}: preserve names generated key(s) "
            f"{', '.join(overlap)}; preserve may only carry foreign keys"
        )
    # Foreign keys are appended AFTER rendering so the owned key keeps its
    # position ahead of them.
    document.update(_preserved(root / str(target.path), target.preserve))
    return json.dumps(document, indent=2, ensure_ascii=spec.ensure_ascii) + "\n"


def render_all(root: Path) -> dict[Path, str]:
    manifest = load_manifest(manifest_path(root))
    outputs: dict[Path, str] = {root / str(t.path): render(t, manifest) for t in manifest.targets}
    if manifest.permissions:
        source = permissions_source(manifest)
        rules = parse_rules(source.path.joinpath(*PERMISSIONS_SOURCE))
        for target in manifest.permissions:
            outputs[root / str(target.path)] = render_permission_target(target, rules, root)
    return outputs


def render_project(root: Path) -> dict[Path, str]:
    config = load_project_config(project_config_path(root))
    project_dir = root / "loadout"

    committed = parse_rules(project_dir / PROJECT_SOURCE)
    local_path = project_dir / PROJECT_LOCAL_SOURCE
    tiers = [committed]
    if local_path.is_file():
        tiers.append(parse_rules(local_path))
    rules = merge_rules(*tiers)

    outputs: dict[Path, str] = {}
    for target in project_targets(config):
        spec = RENDERERS[target.renderer]
        if isinstance(spec, TextSpec):
            outputs[root / str(target.path)] = spec.fn(rules)
        else:
            base: dict[str, Any] = {}
            if target.preserve_foreign:
                base = _load_existing(root / str(target.path))
            document = spec.fn(rules, base)
            outputs[root / str(target.path)] = (
                json.dumps(document, indent=2, ensure_ascii=spec.ensure_ascii) + "\n"
            )
    return outputs


def atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".loadout-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_all(root: Path) -> list[Path]:
    written: list[Path] = []
    for path, content in render_all(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, content)
        written.append(path)
    return written


def check_all(root: Path) -> list[tuple[Path, str, str]]:
    drift: list[tuple[Path, str, str]] = []
    for path, expected in render_all(root).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            drift.append((path, actual, expected))
    return drift
