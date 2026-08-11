from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from .composition import render
from .documents import merge_documents
from .errors import LoadoutError
from .manifest import (
    MANIFEST_NAME,
    InstructionTarget,
    Manifest,
    PermissionTarget,
    load_manifest,
    manifest_path,
    resolve_destination,
)
from .permissions.merge import merge_rules
from .permissions.renderers import RENDERERS, JsonSpec, TextSpec
from .permissions.rules import EMPTY_RULES, Rules, parse_rules
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    load_project_config,
    project_config_path,
    project_targets,
)
from .resolve import SETTINGS, resolve_item
from .sources import Source

PERMISSIONS_SOURCE = ("permissions.toml",)
PROJECT_SOURCE = "permissions.toml"
PROJECT_LOCAL_SOURCE = "permissions.local.toml"


def permission_sources(manifest: Manifest) -> tuple[Source, ...]:
    """Every source offering permissions.toml, in manifest order.

    Order is load-bearing, not incidental: `merge_rules` resolves a decision
    order-independently but keeps emission order from tier order, and OpenCode
    and Pi are last-match-wins. So the manifest's `[[source]]` order is the tier
    order — lowest priority first.
    """
    offering = tuple(
        source
        for source in manifest.sources
        if "permissions" in source.use and (source.path.joinpath(*PERMISSIONS_SOURCE)).is_file()
    )
    if not offering:
        raise LoadoutError(
            "no source provides permissions.toml, but the manifest declares [permissions.*] targets"
        )
    return offering


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
        raise LoadoutError(
            f"{path}: invalid JSON: {error}. This is a generated file; delete it and "
            f"re-run `loadout sync`."
        ) from error
    if not isinstance(document, dict):
        raise LoadoutError(
            f"{path}: existing output must be a JSON object. This is a generated file; "
            f"delete it and re-run `loadout sync`."
        )
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


def _resolve_renderer(name: str, label: str) -> JsonSpec | TextSpec:
    spec = RENDERERS.get(name)
    if spec is None:
        known = ", ".join(sorted(RENDERERS))
        raise LoadoutError(f"{label}: unknown renderer {name!r} (known: {known})")
    return spec


def _serialize_json(document: dict[str, Any], spec: JsonSpec) -> str:
    return json.dumps(document, indent=2, ensure_ascii=spec.ensure_ascii) + "\n"


def settings_document(target: PermissionTarget, manifest: Manifest, root: Path) -> dict[str, Any]:
    """The document this target's renderer writes its own keys into.

    Settings is the **residual** slice, not a peer of the others: permissions
    owns `permissions.allow`/`deny`/`ask`, and settings owns everything else in
    the same file. Each owning slice regenerates its keys unconditionally, so
    generated content can never feed back (ADR 0001) while hand-maintained keys
    survive untouched.

    `base` names a file and `settings` names fragments that compose into one —
    two spellings of the same input, so at most one is set.
    """
    if target.settings:
        parts = [
            _load_base(resolve_item(manifest.sources, name, SETTINGS).path)
            for name in target.settings
        ]
        return merge_documents(*parts)
    return _load_base(root / str(target.base)) if target.base else {}


def render_permission_target(
    target: PermissionTarget, rules: Rules, base: dict[str, Any], root: Path, path: Path
) -> str:
    """Render this target for one output path.

    `path` is the file about to be overwritten, and the only file `preserve` reads.
    Rendering per path rather than once per target is what lets two destinations of
    one target hold different foreign keys — each co-owner writes its own copy.
    """
    spec = _resolve_renderer(target.renderer, f"permissions.{target.name}")
    effective = rules if target.select_all else EMPTY_RULES

    if isinstance(spec, TextSpec):
        return spec.fn(effective)

    document = spec.fn(effective, base)
    overlap = [k for k in target.preserve if k in document]
    if overlap:
        raise LoadoutError(
            f"permissions.{target.name}: preserve names generated key(s) "
            f"{', '.join(overlap)}; preserve may only carry foreign keys"
        )
    # Foreign keys are appended AFTER rendering so the owned key keeps its
    # position ahead of them.
    document.update(_preserved(path, target.preserve))
    return _serialize_json(document, spec)


def _declared_profiles(manifest: Manifest) -> set[str]:
    declared = {t.profile for t in manifest.targets if t.profile}
    declared |= {t.profile for t in manifest.permissions if t.profile}
    return declared


def _selected(target: InstructionTarget | PermissionTarget, profile: str) -> bool:
    return target.profile is None or target.profile == profile


def _claim(path: Path, owner: str, claimed: dict[Path, str]) -> None:
    previous = claimed.get(path)
    if previous is not None:
        raise LoadoutError(f"destination {path} is claimed by both {previous} and {owner}")
    claimed[path] = owner


def _target_label(target: InstructionTarget | PermissionTarget) -> str:
    """How the target is spelled in the manifest, for errors that send the reader there."""
    if isinstance(target, PermissionTarget):
        return f"permissions.{target.name}"
    if target.name:
        return f"instructions.{target.name}"
    return f"instructions[{', '.join(target.fragments)}]"


def _owner_label(target: InstructionTarget | PermissionTarget) -> str:
    if target.path is not None:
        return str(target.path)
    return _target_label(target)


def _fixed(content: str) -> Callable[[Path], str]:
    """An instruction document reads nothing from the file it overwrites, so it is
    rendered once and every path it expands to gets the same bytes."""

    def render_for(_path: Path) -> str:
        return content

    return render_for


def _expand(
    target: InstructionTarget | PermissionTarget,
    render_for: Callable[[Path], str],
    root: Path,
    outputs: dict[Path, str],
    claimed: dict[Path, str],
) -> None:
    owner = _owner_label(target)
    # own_output is claimed too, not just tracked in outputs, so a later target's
    # destination that happens to name this exact path collides like any other.
    # A target with no `output` contributes no output path — only destinations.
    paths: list[Path] = []
    if target.path is not None:
        paths.append(root / str(target.path))
    label = _target_label(target)
    paths.extend(resolve_destination(str(d), label) for d in target.destinations)
    for path in paths:
        _claim(path, owner, claimed)
        outputs[path] = render_for(path)


def declared_profiles(root: Path) -> set[str]:
    """Every profile this root names, plus the implicit 'default'."""
    profiles = {"default"}
    path = manifest_path(root)
    if path.is_file():
        profiles |= _declared_profiles(load_manifest(path))
    return profiles


def render_global(root: Path, profile: str = "default") -> dict[Path, str]:
    manifest = load_manifest(manifest_path(root))
    declared = _declared_profiles(manifest)
    if profile != "default" and profile not in declared:
        known = ", ".join(sorted(declared)) or "none"
        raise LoadoutError(f"unknown profile {profile!r} (declared: {known})")

    outputs: dict[Path, str] = {}
    claimed: dict[Path, str] = {}
    for t in manifest.targets:
        if _selected(t, profile):
            _expand(t, _fixed(render(t, manifest)), root, outputs, claimed)

    selected_permissions = [t for t in manifest.permissions if _selected(t, profile)]
    if selected_permissions:
        tiers = [
            parse_rules(source.path.joinpath(*PERMISSIONS_SOURCE))
            for source in permission_sources(manifest)
        ]
        rules = merge_rules(*tiers)
        for target in selected_permissions:
            base = settings_document(target, manifest, root)
            render_for = partial(render_permission_target, target, rules, base, root)
            _expand(target, render_for, root, outputs, claimed)
    return outputs


def render_all(root: Path, profile: str = "default") -> dict[Path, str]:
    outputs: dict[Path, str] = {}
    has_global = manifest_path(root).is_file()
    has_project = project_config_path(root).is_file()

    if not has_global and not has_project:
        raise LoadoutError(
            f"no manifest found in {root}: expected {MANIFEST_NAME} "
            f"or {PROJECT_DIR}/{PROJECT_CONFIG_NAME}"
        )

    if has_global:
        outputs.update(render_global(root, profile))
    if has_project:
        project_outputs = render_project(root)
        collisions = sorted(str(p) for p in project_outputs if p in outputs)
        if collisions:
            raise LoadoutError(
                f"path collision between global and project scope: {', '.join(collisions)}"
            )
        outputs.update(project_outputs)
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
        spec = _resolve_renderer(target.renderer, f"project target {target.path}")
        if isinstance(spec, TextSpec):
            outputs[root / str(target.path)] = spec.fn(rules)
        else:
            base: dict[str, Any] = {}
            if target.preserve_foreign:
                base = _load_existing(root / str(target.path))
            document = spec.fn(rules, base)
            outputs[root / str(target.path)] = _serialize_json(document, spec)
    return outputs


def atomic_write(path: Path, content: str) -> None:
    # A destination is often a symlink into the user's config repo (the pre-loadout
    # deployment mechanism). os.replace() on a symlink replaces the link itself, not
    # its target — write through the link instead, so the symlink survives sync.
    target = path.resolve() if path.is_symlink() else path
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".loadout-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_all(root: Path, profile: str = "default") -> list[Path]:
    written: list[Path] = []
    for path, content in render_all(root, profile).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, content)
        written.append(path)
    return written


def check_all(root: Path, profile: str = "default") -> list[tuple[Path, str, str]]:
    drift: list[tuple[Path, str, str]] = []
    for path, expected in render_all(root, profile).items():
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            drift.append((path, actual, expected))
    return drift
