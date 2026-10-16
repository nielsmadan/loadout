from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .composition import HEADER, load_fragment, render
from .destinations import resolve_destination
from .documents import merge_documents
from .errors import LoadoutError
from .harnesses import DIRECTORY_SLICES, SHARED_SKILL_READERS
from .machine import is_registered, registered_source
from .manifest import (
    MANIFEST_NAME,
    InstructionTarget,
    Manifest,
    ModuleConfigTarget,
    PermissionTarget,
    SkillsTarget,
    declared_profile_files,
    load_manifest,
    load_profile,
    manifest_path,
)
from .module_config import MODULE_CONFIG_SUBDIR, discover_module_config
from .output import Copied, Merged, Output, output_record_paths
from .permissions.merge import merge_rules
from .permissions.renderers import (
    RENDERERS,
    DocumentJsonSpec,
    DocumentTextSpec,
    JsonSpec,
    MergedJsonSpec,
    MergedTomlSpec,
    TextSpec,
    ValueSpec,
    ValuesSpec,
    evicted_keys,
    render_pi_project,
)
from .permissions.rules import EMPTY_RULES, Rules, parse_rules
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    PROJECT_PRESET,
    ProjectConfig,
    load_project_config,
    project_config_path,
    project_outputs,
    project_slices,
    refuse_escaping,
    refuse_external_links,
)
from .record import owned_now, read_record, render_record
from .resolve import INSTRUCTIONS, SETTINGS, ResolvedItem, json_slice, resolve_item
from .servers import SERVERS_SOURCE, Server, parse_servers
from .skill_policy import SkillPolicy, render_skill_for, selected_skill_agents
from .skills import (
    EXCLUDED_DIRECTORIES,
    SKILL_DOCUMENT,
    Skill,
    check_skill_agents,
    discover_skills,
    render_skill,
)
from .sources import Source, SourceParser, parse_sources, select_overrides
from .subagents import (
    AGENTS_SUBDIR,
    Agent,
    agent_file_name,
    discover_agents,
    parse_agent,
    render_agent,
)
from .surgery import concat_documents
from .template_catalog import Catalog, contribution_paths, load_catalogs
from .templates import resolve_template, resolve_upstream_template
from .toml_paths import overlapping_keys

PERMISSIONS_SOURCE = "permissions.toml"
PROJECT_LOCAL_SOURCE = "permissions.local.toml"
PROJECT_DOCUMENTS = {"hooks": "hooks.json", "plugins": "plugins.json"}


@dataclass(frozen=True)
class RenderContext:
    """What a render reads from outside the source: where a destination lands, where a
    template that is not vendored comes from, how a manifest's sources are found, and
    which root may render global scope."""

    destination: Callable[[str, str], Path] = resolve_destination
    upstream: Callable[[str], ResolvedItem] = resolve_upstream_template
    sources: SourceParser = parse_sources
    registered: Callable[[], Path | None] = registered_source


def refuse_unregistered_global(
    root: Path, context: RenderContext | None = None, *, held: bool = False
) -> None:
    """Global scope writes anywhere under HOME, so it renders only from the source this
    machine registered; a cloned or PR-added `loadout.toml` stops here, before any of it
    is read (ADR 0031). `held` names a manifest that is not on disk, such as a staged one."""
    if (held or manifest_path(root).is_file()) and not is_registered(
        root, (context or RenderContext()).registered()
    ):
        raise LoadoutError(
            f"{root}: this folder holds a global setup ({MANIFEST_NAME}) that isn't this "
            "machine's registered source; register it with `loadout init global <path>`"
        )


@dataclass(frozen=True)
class ProjectTemplates:
    """A project's declared templates, resolved once per render."""

    paths: tuple[Path, ...] = ()
    catalogs: Mapping[Path, Catalog] = field(default_factory=dict)

    def contributions(self, slice_name: str) -> tuple[Path, ...]:
        return contribution_paths(self.paths, slice_name, catalogs=self.catalogs)


def project_templates(
    root: Path, config: ProjectConfig, context: RenderContext
) -> ProjectTemplates:
    paths = tuple(
        resolve_template(name, root, upstream=context.upstream).path for name in config.templates
    )
    return ProjectTemplates(paths, load_catalogs(paths))


def permission_sources(manifest: Manifest) -> tuple[Source, ...]:
    """Every source offering permissions.toml, in manifest order.

    Order is load-bearing, not incidental: `merge_rules` resolves a decision
    order-independently but keeps emission order from tier order, and OpenCode
    and Pi are last-match-wins. So the manifest's `[[source]]` order is the tier
    order — lowest priority first. A source without the file contributes no
    tier, so a manifest with none renders as one whose file is empty.
    """
    return tuple(
        source
        for source in manifest.sources
        if "permissions" in source.use and (source.path / PERMISSIONS_SOURCE).is_file()
    )


def global_rules(manifest: Manifest) -> Rules:
    """Every permission source's rules merged, lowest priority first."""
    return merge_rules(
        *(parse_rules(source.path / PERMISSIONS_SOURCE) for source in permission_sources(manifest))
    )


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


def _resolve_renderer(
    name: str, label: str
) -> (
    JsonSpec
    | TextSpec
    | ValueSpec
    | ValuesSpec
    | DocumentTextSpec
    | DocumentJsonSpec
    | MergedTomlSpec
    | MergedJsonSpec
):
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


def global_servers(manifest: Manifest) -> dict[str, Server]:
    """Every server every source offering mcp.toml declares, last source wins."""
    servers: dict[str, Server] = {}
    for source in manifest.sources:
        if "mcp" in source.use:
            servers.update(parse_servers(source.path / SERVERS_SOURCE))
    return servers


def _permission_content(
    target: PermissionTarget, servers: dict[str, Server], manifest: Manifest
) -> dict[str, Any]:
    """What a permission-shaped target renders from, besides Rules.

    The servers renderers take `dict[str, Server]` rather than a fragment
    composition — there is no `content_slice` to name, since a server has one
    source of truth (mcp.toml), not fragments to merge.
    """
    if target.renderer.endswith("-servers") or target.renderer == "codex-config":
        return dict(servers)
    if target.content_slice is not None:
        return slice_document(target.content, target.content_slice, manifest)
    return {}


def _refuse_composition(
    contributors: list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]],
    path: Path,
    description: str,
    suffix: str,
) -> None:
    """A file with a single owner cannot take a second slice.

    Threading through a renderer that builds its document from scratch would
    silently discard everything rendered before it, so this refuses instead.
    """
    if len(contributors) == 1:
        return
    first = contributors[0][0]
    others = ", ".join(target.name for target, _, _ in contributors[1:])
    raise LoadoutError(
        f"permissions.{first.name}: {path} {description}, so it cannot compose "
        f"with {others}; {suffix}"
    )


def _compose_merged(
    contributors: list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]],
    rules: Rules,
    path: Path,
) -> Merged | None:
    """Every slice writing this file, unioned into one application.

    Applying each slice on its own would mean the second read the first's result —
    ADR 0001's feedback loop, one layer down — so the declared keys are unioned and
    the fragments concatenated, and `write_all` touches the destination once.
    """
    specs = [
        _resolve_renderer(target.renderer, f"permissions.{target.name}")
        for target, _, _ in contributors
    ]
    if not any(isinstance(spec, MergedTomlSpec | MergedJsonSpec) for spec in specs):
        return None

    first = contributors[0][0]
    plain = ", ".join(
        target.name
        for (target, _, _), spec in zip(contributors, specs, strict=True)
        if not isinstance(spec, MergedTomlSpec | MergedJsonSpec)
    )
    if plain:
        raise LoadoutError(
            f"permissions.{first.name}: {path} is merged into a file loadout does not "
            f"own, so it cannot compose with {plain}, which rewrite it whole"
        )

    json_specs = [spec for spec in specs if isinstance(spec, MergedJsonSpec)]
    if json_specs:
        if len(contributors) > 1:
            names = ", ".join(target.name for target, _, _ in contributors)
            raise LoadoutError(
                f"permissions.{first.name}: {path} takes one merged JSON slice, but "
                f"{names} all write it; JSON fragments cannot be concatenated the way "
                f"TOML tables can"
            )
        json_spec = json_specs[0]
        _, _, content = contributors[0]
        document = json.dumps(json_spec.fn(content), indent=2, ensure_ascii=False)
        return Merged(json_spec.owns, document, format="json")

    owned: set[str] = set()
    fragments: list[str] = []
    for (target, _, content), spec in zip(contributors, specs, strict=True):
        assert isinstance(spec, MergedTomlSpec)
        declared = spec.owns(content) if callable(spec.owns) else spec.owns
        clash = overlapping_keys(frozenset(owned), declared)
        if clash:
            raise LoadoutError(
                f"permissions.{target.name}: {sorted(clash)} is already owned by another "
                f"slice writing {path}; two slices declaring one key cannot both be "
                f"stripped and rewritten"
            )
        owned |= declared
        fragments.append(spec.fn(rules, content))
    return Merged(frozenset(owned), concat_documents(tuple(fragments)))


def _apply_value_renderer(
    spec: ValueSpec | ValuesSpec,
    target: PermissionTarget,
    content: dict[str, Any],
    document: dict[str, Any],
    label: str,
) -> None:
    if isinstance(spec, ValueSpec):
        if target.owned_key is None:
            raise LoadoutError(
                f"{label}: its renderer produces one key's value, but the preset "
                f"names no owned_key for it to be written under"
            )
        document[target.owned_key] = spec.fn(content)
        return
    if target.owned_key is not None:
        raise LoadoutError(
            f"{label}: its renderer produces several keys, but the preset "
            f"names owned_key {target.owned_key!r}"
        )
    document.update(spec.fn(content))


def _attach_records(
    document: str | Merged,
    contributors: list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]],
    manifest: Manifest,
) -> str | Merged:
    """Union the recorded keys into what gets stripped, and say what to record next.

    A renderer whose `owns` is a function of its content has *derived* ownership,
    which cannot express a removal on its own: drop a key from the fragment and it
    is no longer named, so nothing strips it and it survives every later run. The
    record is the other half — what was written last time — and the union of the
    two is what makes deletion work (ADR 0017).

    The record sits beside the fragment it belongs to. A slice composing several
    fragments keeps one record, beside the first, because the keys belong to the
    composed document rather than to any one fragment.
    """
    if not isinstance(document, Merged):
        return document

    owned = document.owned
    evicted = document.evicted
    records: list[tuple[Path, str]] = []
    for target, _, content in contributors:
        spec = _resolve_renderer(target.renderer, f"permissions.{target.name}")
        if not isinstance(spec, MergedTomlSpec) or not callable(spec.owns):
            continue
        if not target.content or target.content_slice is None:
            continue
        first = resolve_item(manifest.sources, target.content[0], json_slice(target.content_slice))
        path = first.path.with_suffix(".owned")
        # The keys this fragment manages, which is what `owns` computes — not the
        # fragment's raw keys. `$remove` is vocabulary rather than a destination key,
        # and recording it would have loadout stripping a key named `$remove`.
        present = spec.owns(content)
        recorded = read_record(path)
        clash = overlapping_keys(recorded, owned - present)
        if clash:
            raise LoadoutError(f"{path}: {sorted(clash)} is already owned by another slice")
        owned |= owned_now(recorded, present)
        evicted |= evicted_keys(content)
        records.append((path, render_record(present)))
    if not records:
        return document
    return Merged(owned, document.document, tuple(records), document.format, evicted)


def compose_permission_document(
    contributors: list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]],
    rules: Rules,
    path: Path,
) -> str | Merged:
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
        _refuse_composition(
            contributors, path, "is rendered as text", "a text target owns its whole file"
        )
        return spec.fn(rules if first.select_all else EMPTY_RULES)

    merged = _compose_merged(contributors, rules, path)
    if merged is not None:
        return merged

    if isinstance(spec, DocumentTextSpec | DocumentJsonSpec):
        # A generated adapter is a JavaScript module and `.mcp.json` a whole
        # JSON document — either way there is no second slice to compose into
        # it, and no residual it could preserve.
        _refuse_composition(
            contributors, path, "is rendered as a whole document", "it owns its whole file"
        )
        if isinstance(spec, DocumentTextSpec):
            return spec.fn(contributors[0][2])
        return _serialize_json(spec.fn(contributors[0][2]))

    # The residual is the whole file minus every owned key, and it is the same
    # for each slice of an agent, so it is taken once rather than per slice.
    document: dict[str, Any] = dict(contributors[0][1])
    preserve: tuple[str, ...] = ()
    for target, _, content in contributors:
        label = f"permissions.{target.name}"
        target_spec = _resolve_renderer(target.renderer, label)
        owned_key = target.owned_key
        preserve += target.preserve

        if isinstance(target_spec, ValueSpec | ValuesSpec):
            _apply_value_renderer(target_spec, target, content, document, label)
            continue
        if owned_key is not None:
            raise LoadoutError(
                f"{label}: the preset gives it owned_key {owned_key!r}, so its renderer "
                f"must produce that key's value rather than a whole document"
            )
        if isinstance(
            target_spec,
            TextSpec | DocumentTextSpec | DocumentJsonSpec | MergedTomlSpec | MergedJsonSpec,
        ):
            raise LoadoutError(
                f"{label}: a whole-file renderer cannot compose with another slice writing {path}"
            )
        assert isinstance(target_spec, JsonSpec)
        if len(contributors) > 1 and target_spec.owns_whole_file:
            raise LoadoutError(
                f"{label}: its renderer builds {path} from scratch, so that file has one "
                f"owner and cannot compose with another slice"
            )
        document = target_spec.fn(rules if target.select_all else EMPTY_RULES, document)
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


def selected_target(target: InstructionTarget | PermissionTarget, profile: str) -> bool:
    return target.profile is None or target.profile == profile


@dataclass(frozen=True)
class _Expansion:
    """What a render has produced so far, and which source claimed each path: one
    expansion step adds to both."""

    outputs: dict[Path, Output]
    claimed: dict[Path, str]


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


def _target_paths(
    target: InstructionTarget | PermissionTarget, root: Path, context: RenderContext
) -> list[Path]:
    paths: list[Path] = []
    if target.path is not None:
        paths.append(root / str(target.path))
    label = _target_label(target)
    paths.extend(context.destination(str(d), label) for d in target.destinations)
    return paths


def _require_same_owner(first: PermissionTarget, second: PermissionTarget, path: Path) -> None:
    """Several slices of one agent may compose into one file; two owners may not.

    `agent` is None for the hand-written spelling, where every target names its
    own file, so any second contributor there is the collision it always was.
    """
    if first.harness is None or second.harness is None or first.harness != second.harness:
        raise LoadoutError(
            f"destination {path} is claimed by both permissions.{first.name} "
            f"and permissions.{second.name}"
        )


def _expand(
    target: InstructionTarget | PermissionTarget,
    render_for: Callable[[Path], str],
    root: Path,
    state: _Expansion,
    context: RenderContext,
) -> None:
    outputs, claimed = state.outputs, state.claimed
    owner = _owner_label(target)
    # own_output is claimed too, not just tracked in outputs, so a later target's
    # destination that happens to name this exact path collides like any other.
    # A target with no `output` contributes no output path — only destinations.
    for path in _target_paths(target, root, context):
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


def _owns_its_file(target: PermissionTarget) -> bool:
    """Whether this renderer writes a whole file rather than one key of a foreign one."""
    spec = _resolve_renderer(target.renderer, f"permissions.{target.name}")
    return not isinstance(spec, MergedJsonSpec)


def _rule_less(renderer: str, rules: Rules) -> bool:
    """Whether a whole permission file would hold no rule, and so is not written.

    Global Pi's file is not among these: its empty render still makes `bash` ask.
    """
    if renderer in {"codex", "codex-project"}:
        spec = RENDERERS[renderer]
        assert isinstance(spec, TextSpec)
        return "prefix_rule(" not in spec.fn(rules)
    if renderer == "claude-mcp-permissions":
        return not (rules.mcp_allow or rules.mcp_ask or rules.mcp_deny)
    if renderer == "pi-project":
        permission = render_pi_project(rules, {})["permission"]
        return not (permission["bash"] or permission["mcp"])
    return False


def render_global(
    root: Path, profile: str = "default", context: RenderContext | None = None
) -> dict[Path, Output]:
    context = context or RenderContext()
    refuse_unregistered_global(root, context)
    manifest = load_profile(root, profile, sources=context.sources)
    declared = _declared_profiles(manifest) | declared_profile_files(root)
    if profile != "default" and profile not in declared:
        known = ", ".join(sorted(declared)) or "none"
        raise LoadoutError(f"unknown profile {profile!r} (declared: {known})")

    outputs: dict[Path, Output] = {}
    claimed: dict[Path, str] = {}
    expansion = _Expansion(outputs, claimed)
    for t in manifest.targets:
        if selected_target(t, profile):
            _expand(t, _fixed(render(t, manifest)), root, expansion, context)

    selected_permissions = [t for t in manifest.permissions if selected_target(t, profile)]
    if selected_permissions:
        rules = global_rules(manifest)
        servers = global_servers(manifest)
        groups: dict[Path, list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]]] = {}
        for target in selected_permissions:
            # A source offering no mcp.toml contributes no tier, the same as a
            # template offering no permissions.toml — nothing to write,
            # mirroring render_project's identical guard.
            #
            # A merged slice is the exception and must still write: it owns a key
            # inside a file it does not own, so rendering nothing leaves the last
            # server registered forever. Skipping here is how "delete the only
            # server" silently did nothing.
            #
            # A whole permission file with no rule in it is not written either.
            if _owns_its_file(target) and (
                (target.renderer.endswith("-servers") and not servers)
                or _rule_less(target.renderer, rules if target.select_all else EMPTY_RULES)
            ):
                continue
            content = _permission_content(target, servers, manifest)
            residual = settings_document(target, manifest, root)
            for path in _target_paths(target, root, context):
                group = groups.setdefault(path, [])
                if group:
                    _require_same_owner(group[0][0], target, path)
                else:
                    _claim(path, _owner_label(target), claimed)
                group.append((target, residual, content))
        for path, contributors in groups.items():
            outputs[path] = _attach_records(
                compose_permission_document(contributors, rules, path), contributors, manifest
            )

    _expand_every_skill(manifest, expansion, context)
    _expand_agents(manifest, expansion, context)
    # After skills and agents, so a module-config path that lands on a rendered
    # destination collides with it rather than depending on which ran first.
    for module_config_target in manifest.module_config:
        _expand_module_config(module_config_target, manifest, expansion, context)
    return outputs


SKILLS_SUBDIR = "skills"


def skill_trees(manifest: Manifest) -> tuple[Skill, ...]:
    offerings = [
        (source, discover_skills(source.path / SKILLS_SUBDIR))
        for source in manifest.sources
        if SKILLS_SUBDIR in source.use
    ]
    selected = select_overrides(
        SKILLS_SUBDIR, ((source, {skill.name for skill in skills}) for source, skills in offerings)
    )
    return tuple(
        sorted(
            (
                skill
                for source, skills in offerings
                for skill in skills
                if selected[skill.name] == source
            ),
            key=lambda skill: skill.name,
        )
    )


def _expand_every_skill(
    manifest: Manifest,
    state: _Expansion,
    context: RenderContext,
) -> None:
    if not manifest.skills:
        return
    trees = skill_trees(manifest)
    _check_skill_agents(trees, manifest.skill_policies, source_agents(manifest))
    for skills_target in manifest.skills:
        _expand_skills(skills_target, manifest, trees, state, context)


def _expand_skills(
    target: SkillsTarget,
    manifest: Manifest,
    trees: tuple[Skill, ...],
    state: _Expansion,
    context: RenderContext,
) -> None:
    available = tuple(item.harness for item in manifest.skills)
    for destination in target.destinations:
        base = context.destination(str(destination), f"{target.harness}.skills")
        for skill in trees:
            if target.harness not in selected_skill_agents(
                skill.name, available, manifest.skill_policies
            ):
                continue
            render = render_skill_for(skill.name, manifest.skill_policies)
            _write_skill(skill, target.harness, base, state, render=render)


def _check_skill_agents(
    trees: tuple[Skill, ...], policies: tuple[SkillPolicy, ...], agents: tuple[Agent, ...]
) -> None:
    """A copied skill is the user's document as written, so only a rendered one is held
    to naming an agent loadout defines."""
    rendered = tuple(skill for skill in trees if render_skill_for(skill.name, policies))
    check_skill_agents(rendered, frozenset(agent.name for agent in agents))


def source_agents(manifest: Manifest) -> tuple[Agent, ...]:
    offerings = [
        (source, discover_agents(source.path / AGENTS_SUBDIR))
        for source in manifest.sources
        if AGENTS_SUBDIR in source.use
    ]
    selected = select_overrides(
        AGENTS_SUBDIR, ((source, {path.stem for path in paths}) for source, paths in offerings)
    )
    return tuple(
        parse_agent(path)
        for path in sorted(
            (
                path
                for source, paths in offerings
                for path in paths
                if selected[path.stem] == source
            ),
            key=lambda path: path.stem,
        )
    )


def _expand_agents(
    manifest: Manifest,
    state: _Expansion,
    context: RenderContext,
) -> None:
    agents = source_agents(manifest) if manifest.agents else ()
    for target in manifest.agents:
        label = f"{target.harness}.{AGENTS_SUBDIR}"
        for destination in target.destinations:
            base = context.destination(str(destination), label)
            _write_agents(agents, target.harness, base, state)


def _write_agents(
    agents: tuple[Agent, ...],
    harness: str,
    base: Path,
    state: _Expansion,
) -> None:
    outputs, claimed = state.outputs, state.claimed
    for agent in agents:
        path = base / agent_file_name(agent.name, harness)
        _claim(path, f"{harness}.{AGENTS_SUBDIR}", claimed)
        outputs[path] = render_agent(agent, harness)


def _expand_project_agents(root: Path, config: ProjectConfig, state: _Expansion) -> None:
    agents = project_agents(root)
    for harness, slice_name, spec in project_slices(config.harnesses):
        if slice_name == AGENTS_SUBDIR and spec.output is not None:
            _write_agents(agents, harness, root / spec.output, state)


def project_agents(root: Path) -> tuple[Agent, ...]:
    return tuple(parse_agent(path) for path in discover_agents(root / PROJECT_DIR / AGENTS_SUBDIR))


def module_config_files(manifest: Manifest, agent: str) -> tuple[tuple[PurePosixPath, Path], ...]:
    seen: dict[PurePosixPath, str] = {}
    collected: dict[PurePosixPath, Path] = {}
    for source in manifest.sources:
        if MODULE_CONFIG_SUBDIR not in source.use:
            continue
        agent_root = source.path / MODULE_CONFIG_SUBDIR / agent
        offered = discover_module_config(agent_root)
        overrides = {
            PurePosixPath(name).relative_to(agent)
            for name in source.override_names(MODULE_CONFIG_SUBDIR)
            if PurePosixPath(name).parts[0] == agent
        }
        for relative in sorted(overrides):
            if relative not in offered:
                raise LoadoutError(
                    f"source {source.name!r}: override {agent}/{relative} has no replacing item"
                )
            if relative not in seen:
                raise LoadoutError(
                    f"source {source.name!r}: override {agent}/{relative} has no earlier contender"
                )
        for relative in offered:
            if relative in seen and relative not in overrides:
                raise LoadoutError(
                    f"module config {str(relative)!r} for {agent} is offered by both "
                    f"{seen[relative]!r} and {source.name!r}; declare "
                    "source.overrides.module-config, rename one, or drop it from a source's `use`"
                )
            seen[relative] = source.name
            collected[relative] = agent_root / Path(str(relative))
    return tuple(sorted(collected.items(), key=lambda item: str(item[0])))


def _expand_module_config(
    target: ModuleConfigTarget,
    manifest: Manifest,
    state: _Expansion,
    context: RenderContext,
) -> None:
    outputs, claimed = state.outputs, state.claimed
    files = module_config_files(manifest, target.harness)
    if not files:
        return
    label = f"{target.harness}.{MODULE_CONFIG_SUBDIR}"
    for destination in target.destinations:
        base = context.destination(str(destination), label)
        for relative, source in files:
            path = base / Path(str(relative))
            _claim(path, label, claimed)
            outputs[path] = Copied(source=source)


def render_all(
    root: Path, profile: str = "default", context: RenderContext | None = None
) -> dict[Path, Output]:
    context = context or RenderContext()
    outputs: dict[Path, Output] = {}
    has_global = manifest_path(root).is_file()
    has_project = project_config_path(root).is_file()

    if not has_global and not has_project:
        raise LoadoutError(
            f"no manifest found in {root}: expected {MANIFEST_NAME} "
            f"or {PROJECT_DIR}/{PROJECT_CONFIG_NAME}"
        )

    global_outputs = render_global(root, profile, context) if has_global else {}
    outputs.update(global_outputs)
    if has_project:
        project_outputs = render_project(root, context)
        collisions = sorted(str(p) for p in project_outputs if p in outputs)
        if collisions:
            raise LoadoutError(
                f"path collision between global and project scope: {', '.join(collisions)}"
            )
        outputs.update(project_outputs)
    return outputs


def render_project(root: Path, context: RenderContext | None = None) -> dict[Path, Output]:
    refuse_external_links(root, EXCLUDED_DIRECTORIES)
    config = load_project_config(project_config_path(root))
    # Before any output is read: a shared document's keys loadout does not own are read
    # from disk, and parsing a linked one would tell what its target holds.
    shared = [
        root / spec.output
        for _, _, spec in project_slices(config.harnesses)
        if spec.preserve_foreign and spec.output is not None
    ]
    refuse_escaping(root, shared, [root / output for output in project_outputs(config)])
    templates = project_templates(root, config, context or RenderContext())
    outputs = _render_project_slices(root, config, templates)
    # Before anything compares or shows these: a diff of a file outside the project
    # would print it.
    refuse_escaping(root, (*outputs, *output_record_paths(outputs)), outputs)
    return outputs


def project_rules(root: Path, templates: ProjectTemplates) -> Rules:
    """The permission tiers project scope merges, lowest first.

    Templates are the lowest tier, in declared order: a template describes a kind
    of work, and anything the project itself says outranks it. A template offering
    no permissions contributes no tier rather than failing — its `use` already says
    which slices it offers, and one slice is a legitimate template.

    Shared with `project_notices` so the verdict a notice names is the one that
    actually rendered, the way `permission_sources` guarantees it at global scope.
    """
    project_dir = root / PROJECT_DIR
    tiers: list[Rules] = [
        parse_rules(contributed) for contributed in templates.contributions("permissions")
    ]
    for path in (project_dir / PERMISSIONS_SOURCE, project_dir / PROJECT_LOCAL_SOURCE):
        if path.is_file():
            tiers.append(parse_rules(path))
    return merge_rules(*tiers)


def _retired_keys(
    root: Path, config: ProjectConfig, contributes: Callable[[str], bool]
) -> dict[Path, set[str]]:
    """Keys a slice owns but will not write this time, by path.

    A co-owner of the same path runs with `preserve_foreign`, so anything left
    behind is read back as foreign and survives every later render — delete the
    fragment and its keys stay forever, which is the ADR 0001 feed-back shape
    `render_global` already guards against. Retiring them is what makes a removal
    take effect. Keys of slices that *do* run are deliberately absent: rewriting
    them in the base keeps their position, and key order is load-bearing.
    """
    retired: dict[Path, set[str]] = {}
    for _agent, slice_name, spec in project_slices(config.harnesses):
        if spec.output is None or contributes(slice_name):
            continue
        keys = retired.setdefault(root / spec.output, set())
        if spec.owned_key:
            keys.add(spec.owned_key)
        renderer = RENDERERS.get(spec.renderer or "")
        if isinstance(renderer, ValuesSpec):
            keys.update(renderer.owns)
    return retired


def project_owned_keys(harnesses: tuple[str, ...], output: str) -> frozenset[str]:
    """Every top-level key some slice of these harnesses writes into one project file."""
    keys: set[str] = set()
    for _agent, _slice, spec in project_slices(harnesses):
        if spec.output != output:
            continue
        if spec.owned_key:
            keys.add(spec.owned_key)
        renderer = RENDERERS.get(spec.renderer or "")
        if isinstance(renderer, ValuesSpec):
            keys.update(renderer.owns)
        elif isinstance(renderer, JsonSpec):
            keys.update(renderer.fn(EMPTY_RULES, {}))
    return frozenset(keys)


def _render_project_slices(
    root: Path, config: ProjectConfig, templates: ProjectTemplates
) -> dict[Path, Output]:
    rules = project_rules(root, templates)

    instructions = project_instructions(root, config, templates)
    trees = project_skill_trees(root, templates)
    servers = project_servers(root, templates)
    project_documents = {
        name: project_slice_document(root, name, templates) for name in PROJECT_DOCUMENTS
    }

    def _contributes(slice_name: str) -> bool:
        """Whether this slice has anything to write this time."""
        if slice_name == "mcp":
            return bool(servers)
        if slice_name in project_documents:
            return project_documents[slice_name][1]
        return True

    retired = _retired_keys(root, config, _contributes)

    outputs: dict[Path, Output] = {}
    # Project scope claims its paths for the same reason global scope does, and
    # for one it does not: the `.agents/` convention table reads as an invitation
    # to serve two harnesses from one directory, which is right for instructions
    # and wrong for skills. `opencode.md` forbids exactly that edit in prose, and
    # prose does not stop anyone — without this the second agent silently
    # overwrote the first and the render succeeded.
    claimed: dict[Path, str] = {}
    expansion = _Expansion(outputs, claimed)
    # Grouped by path, the way render_global groups its contributors: OpenCode's
    # mcp slice writes the "mcp" key into the same opencode.json the permissions
    # slice writes "permission" into, so both must reach compose_permission_document
    # together rather than each overwriting the other's call.
    groups: dict[Path, list[tuple[PermissionTarget, dict[str, Any], dict[str, Any]]]] = {}
    _check_skill_agents(trees, config.skill_policies, project_agents(root))
    _expand_project_catalog(root, config, trees, expansion)
    for agent, slice_name, spec in project_slices(config.harnesses):
        if spec.output is None:
            continue
        path = root / spec.output
        if slice_name in DIRECTORY_SLICES:
            # Directories: skills went through the catalog above, agents below.
            continue
        if slice_name == "instructions":
            # Several agents name one document and that is not a collision here:
            # the order is the project's, not the agent's, so every one of them
            # renders the same bytes. Skipping the repeat rather than claiming
            # the path is what makes `AGENTS.md` shared instead of contested.
            # Beside another harness, Claude's CLAUDE.md imports it instead.
            if instructions is not None:
                outputs[path] = (
                    CLAUDE_IMPORT
                    if agent == "claude" and _imports_agents(root, config)
                    else instructions
                )
            continue
        if not _contributes(slice_name):
            # A source offering no mcp.toml contributes no tier, the same as a
            # template offering no permissions.toml — nothing to write. The keys
            # it owns were retired above rather than left in the co-owner's base.
            continue
        if _rule_less(spec.renderer or "", rules):
            continue
        owner = f"{agent}.{slice_name}"
        contributor = PermissionTarget(
            harness=agent,
            name=agent if slice_name == "permissions" else f"{agent}-{slice_name}",
            path=None,
            renderer=spec.renderer or "",
            owned_key=spec.owned_key,
            content_slice=spec.source_slice,
        )
        residual = _project_residual(root, agent, path) if spec.preserve_foreign else {}
        if drop := retired.get(path):
            residual = {key: value for key, value in residual.items() if key not in drop}
        content = (
            dict(servers)
            if slice_name == "mcp"
            else project_documents[slice_name][0]
            if slice_name in project_documents
            else {}
        )
        group = groups.setdefault(path, [])
        if group:
            _require_same_owner(group[0][0], contributor, path)
        else:
            _claim(path, owner, claimed)
        group.append((contributor, residual, content))
    _expand_project_agents(root, config, expansion)
    for path, contributors in groups.items():
        # No records here: a record is tied to a source-slice fragment, and project
        # scope renders from the project's own config rather than the global
        # manifest's sources. No project slice has derived ownership today.
        outputs[path] = compose_permission_document(contributors, rules, path)
    return outputs


PROJECT_SETTINGS_DIR = "settings"


def project_settings_path(root: Path, agent: str) -> Path:
    return root / PROJECT_DIR / PROJECT_SETTINGS_DIR / f"{agent}.json"


def _project_residual(root: Path, agent: str, path: Path) -> dict[str, Any]:
    """The keys of a co-owned file no slice writes: the project's settings source when it
    has one, so a clone renders them too, else whatever the file already holds."""
    settings = project_settings_path(root, agent)
    return _load_base(settings) if settings.is_file() else _load_existing(path)


def project_slice_document(
    root: Path, slice_name: str, templates: ProjectTemplates
) -> tuple[dict[str, Any], bool]:
    paths = list(templates.contributions(slice_name))
    local = root / PROJECT_DIR / PROJECT_DOCUMENTS[slice_name]
    if local.is_file():
        paths.append(local)
    return merge_documents(*(_load_base(path) for path in paths)), bool(paths)


def project_skill_trees(root: Path, templates: ProjectTemplates) -> tuple[Skill, ...]:
    """Collect skill trees in template order, then replace matching names from the project."""
    collected: dict[str, Skill] = {}
    for template in templates.paths:
        skills = (
            templates.catalogs[template].skills()
            if template in templates.catalogs
            else discover_skills(template / SKILLS_SUBDIR)
        )
        for skill in skills:
            collected[skill.name] = skill
    for skill in discover_skills(root / PROJECT_DIR / SKILLS_SUBDIR):
        collected[skill.name] = skill
    return tuple(sorted(collected.values(), key=lambda s: s.name))


def project_servers(root: Path, templates: ProjectTemplates) -> dict[str, Server]:
    """Every server this project defines, templates first and the project last.

    Same tier rule as permissions, instructions and skills: a template is
    declared to sit beneath the project, so a project server of the same name
    replaces the template's rather than merging with it.
    """
    collected: dict[str, Server] = {}
    for contributed in templates.contributions("mcp"):
        for server_name, server in parse_servers(contributed).items():
            collected[server_name] = server
    for server_name, server in parse_servers(root / PROJECT_DIR / SERVERS_SOURCE).items():
        collected[server_name] = server
    return collected


def _write_skill(
    skill: Skill,
    agent: str,
    base: Path,
    state: _Expansion,
    *,
    render: bool = True,
) -> None:
    outputs, claimed = state.outputs, state.claimed
    directory = base / skill.name
    document = directory / SKILL_DOCUMENT
    _claim(document, f"{agent}.skills", claimed)
    outputs[document] = render_skill(skill, agent) if render else Copied(skill.document)
    for relative in skill.supporting:
        path = directory / relative
        _claim(path, f"{agent}.skills", claimed)
        outputs[path] = Copied(source=skill.supporting_root / relative)


def _expand_project_catalog(
    root: Path,
    config: ProjectConfig,
    trees: tuple[Skill, ...],
    state: _Expansion,
) -> None:
    available = tuple(agent for agent in config.harnesses if "skills" in PROJECT_PRESET[agent])
    for skill in trees:
        selected = selected_skill_agents(skill.name, available, config.skill_policies)
        should_render = render_skill_for(skill.name, config.skill_policies)
        remaining = list(selected)
        convention = frozenset(SHARED_SKILL_READERS)
        if "codex" in selected:
            shared = convention.intersection(selected)
            variants = {
                agent: render_skill(skill, agent) if should_render else skill.document.read_bytes()
                for agent in shared
            }
            if len(variants) > 1 and len(set(variants.values())) == 1:
                remaining = [agent for agent in remaining if agent not in convention]
                remaining.insert(0, "codex")
        for agent in remaining:
            spec = PROJECT_PRESET[agent]["skills"]
            assert spec.output is not None
            _write_skill(skill, agent, root / spec.output, state, render=should_render)


# Claude reads AGENTS.md only when no CLAUDE.md is on the path, so with another
# harness present the content lives in AGENTS.md and CLAUDE.md imports it.
CLAUDE_IMPORT = f"{HEADER}\n\n@AGENTS.md\n"


def _imports_agents(root: Path, config: ProjectConfig) -> bool:
    """A CLAUDE.md linked to AGENTS.md is that document; writing the stub through
    the link would replace the content."""
    claude = root / "CLAUDE.md"
    linked = claude.is_symlink() and claude.resolve() == (root / "AGENTS.md").resolve()
    return set(config.harnesses) != {"claude"} and not linked


def project_instructions(
    root: Path, config: ProjectConfig, templates: ProjectTemplates
) -> str | None:
    """The project's instruction document, or None when it declares none.

    Templates concatenate first and the project's own fragments after, matching
    the permission tiers: a template describes a kind of work and anything the
    project itself says outranks it — which for prose means it comes last and is
    read last. Selecting a template includes its instruction parts automatically.
    """
    source = Source(name=PROJECT_DIR, path=root / PROJECT_DIR, use=frozenset({INSTRUCTIONS.use}))
    blocks = [
        *(load_fragment(contributed) for contributed in templates.contributions("instructions")),
        *(
            load_fragment(resolve_item((source,), name, INSTRUCTIONS).path)
            for name in config.instructions
        ),
    ]
    if not blocks:
        return None
    return "\n\n".join([HEADER, *blocks]) + "\n"
