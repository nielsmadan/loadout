from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .composition import render
from .errors import LoadoutError
from .manifest import Manifest, PermissionTarget, load_manifest, manifest_path
from .permissions.renderers import RENDERERS, TextSpec
from .permissions.rules import EMPTY_RULES, Rules, parse_rules
from .sources import Source

PERMISSIONS_SOURCE = ("permissions", "permissions.toml")


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
    return document


def _preserved(path: Path, keys: tuple[str, ...]) -> dict[str, Any]:
    if not keys or not path.is_file():
        return {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(existing, dict):
        return {}
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
