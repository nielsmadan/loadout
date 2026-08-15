from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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
    SkillsTarget,
    declared_profile_files,
    load_manifest,
    load_profile,
    manifest_path,
    resolve_destination,
)
from .permissions.merge import merge_rules
from .permissions.renderers import RENDERERS, JsonSpec, TextSpec, ValueSpec
from .permissions.rules import EMPTY_RULES, Rules, parse_rules
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    load_project_config,
    project_config_path,
    project_targets,
)
from .resolve import SETTINGS, json_slice, resolve_item
from .skills import SKILL_DOCUMENT, Skill, discover_skills, render_skill
from .sources import Source

PERMISSIONS_SOURCE = ("permissions.toml",)
PROJECT_SOURCE = "permissions.toml"
PROJECT_LOCAL_SOURCE = "permissions.local.toml"


@dataclass(frozen=True)
class Copied:
    """A file reproduced from a source path rather than rendered from rules.

    A skill is a tree and only `SKILL.md` goes through composition; the rest is
    carried across untouched. Naming the source instead of its decoded text is
    what lets a byte be a byte: `scripts/` files are executable in three skills
    today, and a mode does not survive a `str`.
    """

    source: Path


Output = str | Copied


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


def _resolve_renderer(name: str, label: str) -> JsonSpec | TextSpec | ValueSpec:
    spec = RENDERERS.get(name)
    if spec is None:
        known = ", ".join(sorted(RENDERERS))
        raise LoadoutError(f"{label}: unknown renderer {name!r} (known: {known})")
    return spec


def _serialize_json(document: dict[str, Any], ensure_ascii: bool = False) -> str:
    """Serialisation is a property of the file, not of one contributing slice.

    A document composed only of key contributors has no transformer to take a
    setting from, so the default stands rather than being inherited from
    whichever slice happened to run.
    """
    return json.dumps(document, indent=2, ensure_ascii=ensure_ascii) + "\n"


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


def slice_document(names: tuple[str, ...], slice_name: str, manifest: Manifest) -> dict[str, Any]:
    """Compose one slice's fragments into a document."""
    kind = json_slice(slice_name)
    return merge_documents(
        *(_load_base(resolve_item(manifest.sources, name, kind).path) for name in names)
    )


def compose_permission_document(
    contributors: list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]],
    rules: Rules,
    path: Path,
) -> str:
    """Render every slice that writes this one file, composed into one document.

    Slices **thread** rather than merge: each renderer takes the document built
    so far and returns it with its own key written in, preserving everything
    else. That is the contract renderers already had, and it is why composition
    needs no merge rule — `render_claude` keeps `permissions` at its position in
    the residual and puts the owned lists ahead of hand-maintained keys inside
    it, which merging separate documents would undo.

    **Order is residual-first, and it is load-bearing.** The settings slice
    supplies the starting document; every owning slice overwrites its own key
    afterwards, unconditionally. Run the other way round, a stale `hooks` key
    left in a settings fragment would win against the hooks slice — ADR 0001
    feeding back through a side door. Extraction produces exactly that fragment
    on first run (spec 2), so this is a real case rather than a hypothetical.
    """
    first, _, _ = contributors[0]
    spec = _resolve_renderer(first.renderer, f"permissions.{first.name}")

    if isinstance(spec, TextSpec):
        if len(contributors) > 1:
            others = ", ".join(t.name for t, _, _ in contributors[1:])
            raise LoadoutError(
                f"permissions.{first.name}: {path} is rendered as text, so it cannot "
                f"compose with {others}; a text target owns its whole file"
            )
        return spec.fn(rules if first.select_all else EMPTY_RULES)

    # The residual is the whole file minus every owned key, and it is the same
    # for each slice of an agent, so it is taken once rather than per slice.
    document: dict[str, Any] = dict(contributors[0][1])
    preserve: tuple[str, ...] = ()
    for target, _, content in contributors:
        label = f"permissions.{target.name}"
        target_spec = _resolve_renderer(target.renderer, label)
        owned_key = target.owned_key

        if isinstance(target_spec, ValueSpec):
            if owned_key is None:
                raise LoadoutError(
                    f"{label}: its renderer produces one key's value, but the preset "
                    f"names no owned_key for it to be written under"
                )
            document[owned_key] = target_spec.fn(content)
            continue
        if owned_key is not None:
            raise LoadoutError(
                f"{label}: the preset gives it owned_key {owned_key!r}, so its renderer "
                f"must produce that key's value rather than a whole document"
            )
        if isinstance(target_spec, TextSpec):
            raise LoadoutError(
                f"{label}: a text renderer cannot compose with another slice writing {path}"
            )
        if len(contributors) > 1 and target_spec.owns_whole_file:
            raise LoadoutError(
                f"{label}: its renderer builds {path} from scratch, so that file has one "
                f"owner and cannot compose with another slice"
            )
        document = target_spec.fn(rules if target.select_all else EMPTY_RULES, document)
        preserve += target.preserve
        spec = target_spec

    overlap = [k for k in preserve if k in document]
    if overlap:
        raise LoadoutError(
            f"permissions.{first.name}: preserve names generated key(s) "
            f"{', '.join(overlap)}; preserve may only carry foreign keys"
        )
    # Foreign keys are appended AFTER rendering so the owned key keeps its
    # position ahead of them.
    document.update(_preserved(path, preserve))
    return _serialize_json(document, ensure_ascii=isinstance(spec, JsonSpec) and spec.ensure_ascii)


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


def _target_paths(target: InstructionTarget | PermissionTarget, root: Path) -> list[Path]:
    paths: list[Path] = []
    if target.path is not None:
        paths.append(root / str(target.path))
    label = _target_label(target)
    paths.extend(resolve_destination(str(d), label) for d in target.destinations)
    return paths


def _require_same_owner(first: PermissionTarget, second: PermissionTarget, path: Path) -> None:
    """Several slices of one agent may compose into one file; two owners may not.

    `agent` is None for the hand-written spelling, where every target names its
    own file, so any second contributor there is the collision it always was.
    """
    if first.agent is None or second.agent is None or first.agent != second.agent:
        raise LoadoutError(
            f"destination {path} is claimed by both permissions.{first.name} "
            f"and permissions.{second.name}"
        )


def _expand(
    target: InstructionTarget | PermissionTarget,
    render_for: Callable[[Path], str],
    root: Path,
    outputs: dict[Path, Output],
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
        profiles |= declared_profile_files(root)
    return profiles


def render_global(root: Path, profile: str = "default") -> dict[Path, Output]:
    manifest = load_profile(root, profile)
    declared = _declared_profiles(manifest) | declared_profile_files(root)
    if profile != "default" and profile not in declared:
        known = ", ".join(sorted(declared)) or "none"
        raise LoadoutError(f"unknown profile {profile!r} (declared: {known})")

    outputs: dict[Path, Output] = {}
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
        groups: dict[Path, list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]]] = {}
        for target in selected_permissions:
            residual = settings_document(target, manifest, root)
            content = (
                slice_document(target.content, target.content_slice, manifest)
                if target.content_slice is not None
                else {}
            )
            for path in _target_paths(target, root):
                group = groups.setdefault(path, [])
                if group:
                    _require_same_owner(group[0][0], target, path)
                else:
                    _claim(path, _owner_label(target), claimed)
                group.append((target, residual, content))
        for path, contributors in groups.items():
            outputs[path] = compose_permission_document(contributors, rules, path)

    for skills_target in manifest.skills:
        _expand_skills(skills_target, manifest, outputs, claimed)
    return outputs


SKILLS_SUBDIR = "skills"


def skill_trees(manifest: Manifest) -> tuple[Skill, ...]:
    """Every skill offered by every source, by name, rejecting collisions.

    Two sources offering the same skill is ambiguous in the same way two sources
    offering one fragment name is, and is refused for the same reason: silently
    preferring one would make the winner depend on manifest order rather than on
    anything the author wrote.
    """
    seen: dict[str, str] = {}
    collected: list[Skill] = []
    for source in manifest.sources:
        if SKILLS_SUBDIR not in source.use:
            continue
        for skill in discover_skills(source.path / SKILLS_SUBDIR):
            if skill.name in seen:
                raise LoadoutError(
                    f"skill {skill.name!r} is offered by both {seen[skill.name]!r} and "
                    f"{source.name!r}; rename one, or drop it from a source's `use`"
                )
            seen[skill.name] = source.name
            collected.append(skill)
    return tuple(sorted(collected, key=lambda s: s.name))


def _expand_skills(
    target: SkillsTarget,
    manifest: Manifest,
    outputs: dict[Path, Output],
    claimed: dict[Path, str],
) -> None:
    trees = skill_trees(manifest)
    if not trees:
        return
    for destination in target.destinations:
        base = resolve_destination(str(destination), f"{target.agent}.skills")
        for skill in trees:
            directory = base / skill.name
            document = directory / SKILL_DOCUMENT
            _claim(document, f"{target.agent}.skills", claimed)
            outputs[document] = render_skill(skill, target.agent)
            for relative in skill.supporting:
                path = directory / relative
                _claim(path, f"{target.agent}.skills", claimed)
                outputs[path] = Copied(source=skill.document.parent / relative)


def render_all(root: Path, profile: str = "default") -> dict[Path, Output]:
    outputs: dict[Path, Output] = {}
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


def render_project(root: Path) -> dict[Path, Output]:
    config = load_project_config(project_config_path(root))
    project_dir = root / "loadout"

    committed = parse_rules(project_dir / PROJECT_SOURCE)
    local_path = project_dir / PROJECT_LOCAL_SOURCE
    tiers = [committed]
    if local_path.is_file():
        tiers.append(parse_rules(local_path))
    rules = merge_rules(*tiers)

    outputs: dict[Path, Output] = {}
    for target in project_targets(config):
        label = f"project target {target.path}"
        spec = _resolve_renderer(target.renderer, label)
        if isinstance(spec, ValueSpec):
            raise LoadoutError(
                f"{label}: a value renderer contributes one key of a composed document, "
                f"and project scope renders one target per file"
            )
        if isinstance(spec, TextSpec):
            outputs[root / str(target.path)] = spec.fn(rules)
        else:
            base: dict[str, Any] = {}
            if target.preserve_foreign:
                base = _load_existing(root / str(target.path))
            document = spec.fn(rules, base)
            outputs[root / str(target.path)] = _serialize_json(document, spec.ensure_ascii)
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


def atomic_copy(path: Path, source: Path) -> None:
    target = path.resolve() if path.is_symlink() else path
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".loadout-")
    try:
        os.close(fd)
        # copymode after the bytes, before the rename: an executable script must
        # never be observable at its destination without its exec bit.
        shutil.copyfile(source, tmp)
        shutil.copymode(source, tmp)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_all(root: Path, profile: str = "default") -> list[Path]:
    written: list[Path] = []
    for path, content in render_all(root, profile).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, Copied):
            atomic_copy(path, content.source)
        else:
            atomic_write(path, content)
        written.append(path)
    return written


def check_all(root: Path, profile: str = "default") -> list[tuple[Path, str, str]]:
    drift: list[tuple[Path, str, str]] = []
    for path, expected in render_all(root, profile).items():
        if isinstance(expected, Copied):
            if _copy_drifted(path, expected.source):
                drift.append((path, _describe(path), _describe(expected.source)))
            continue
        actual = path.read_text(encoding="utf-8") if path.is_file() else ""
        if actual != expected:
            drift.append((path, actual, expected))
    return drift


def _copy_drifted(path: Path, source: Path) -> bool:
    if not path.is_file():
        return True
    if path.read_bytes() != source.read_bytes():
        return True
    return _executable(path) != _executable(source)


def _executable(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def _describe(path: Path) -> str:
    # Drift on a copied file reports a summary, not the content: a tree carries
    # binaries, and a byte diff of one is noise rather than a review.
    if not path.is_file():
        return "(absent)"
    suffix = " (executable)" if _executable(path) else ""
    return f"{path.stat().st_size} bytes{suffix}"
