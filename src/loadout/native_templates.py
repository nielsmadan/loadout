from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

import tomlkit

from .artifacts import (
    Artifact,
    Artifacts,
    Copied,
    FrozenFile,
    Output,
    _no_symlinks,
    _regular_file,
    _source,
    part_document,
    render_artifact,
    render_artifacts,
    render_document,
    validate_artifact_paths,
)
from .errors import LoadoutError
from .extract import CODEX_MCP_CATEGORY, CODEX_MCP_KEYS, EXTRACTORS, extract_codex_mcp
from .permissions.merge import merge_rules
from .permissions.renderers import (
    RENDERERS,
    JsonSpec,
    TextSpec,
    codex_mcp_policy,
    render_codex_config,
)
from .permissions.rules import DECISIONS, EMPTY_RULES, Rules, parse_rules
from .project import TEMPLATE_INSTRUCTIONS, ProjectConfig, load_project_config, project_config_path
from .resolve import ResolvedItem
from .servers import (
    Server,
    parse_servers,
    render_claude_project_servers,
    render_opencode_servers,
)
from .skills import SKILL_DOCUMENT, Skill, render_skill
from .template_catalog import Catalog, contribution_paths, load_catalog, load_catalogs
from .templates import resolve_template, template_files


def native_template_prefix(
    config: ProjectConfig,
    templates: tuple[ResolvedItem, ...],
    *,
    catalogs: Mapping[Path, Catalog] | None = None,
) -> bytes:
    loaded = load_catalogs(t.path for t in templates) if catalogs is None else catalogs
    blocks = []
    seen: set[Path] = set()
    for template in templates:
        if template.path.is_file():
            for path in loaded[template.path].paths("instructions"):
                if path not in seen:
                    seen.add(path)
                    if text := path.read_text(encoding="utf-8").strip():
                        blocks.append(text)
            continue
        validate_template_tree(template.path)
        for relative in template_files(template.path):
            path = template.path / relative
            _no_symlinks(path, template.path)
            content = path.read_bytes()
            if relative == Path(TEMPLATE_INSTRUCTIONS):
                try:
                    text = content.decode("utf-8").strip()
                except UnicodeError as error:
                    raise LoadoutError(
                        f"template {template.name!r}: instructions.md must be UTF-8"
                    ) from error
                if text:
                    blocks.append(text)
            elif not _empty_scaffold(relative, content):
                category = relative.parts[0].removesuffix(".toml")
                raise LoadoutError(
                    f"template {template.name!r}: populated {category} contribution at {relative} "
                    "cannot compose with native artifact routes. Move this contribution into an "
                    "explicit project artifact source and remove it from the template before selecting it."
                )
    if not blocks:
        return b""
    routes = config.artifacts.records if config.artifacts else ()
    consumers = {a for route in routes if route.template_instructions for a in route.agents}
    missing = set(config.harnesses) - consumers
    if missing:
        raise LoadoutError(
            f"template instructions have no opted-in artifact route for {', '.join(sorted(missing))}. "
            "Set template_instructions = true on each intended required project copy/text "
            "instruction route in artifacts.toml, then retry the template command."
        )
    return ("\n\n".join(blocks) + "\n\n").encode()


def validate_template_tree(tree: Path) -> None:
    if tree.is_file():
        load_catalog(tree)
        return
    _no_symlinks(tree, tree)
    for path in tree.rglob("*"):
        _no_symlinks(path, tree)


def _empty_scaffold(relative: Path, content: bytes) -> bool:
    if relative.name == ".gitkeep":
        return not content.strip()
    if relative.as_posix() in {"permissions.toml", "mcp.toml"}:
        try:
            return not tomllib.loads(content.decode())
        except (ValueError, UnicodeError):
            return False
    return False


def validate_template_change(root: Path, name: str, template: ResolvedItem) -> None:
    config = load_project_config(project_config_path(root))
    names = tuple(dict.fromkeys((*config.templates, name)))
    config = replace(config, templates=names)
    resolved = tuple(
        template if declared == name else resolve_template(declared, root) for declared in names
    )
    if config.presets:
        load_catalogs(item.path for item in resolved)
        return
    render_native_templates(
        root,
        config,
        resolved,
        source_inputs=(
            project_config_path(root).parent,
            *(t.path.parent if t.path.is_file() else t.path for t in resolved),
        ),
    )


def _permission_document(
    renderer: str, document: dict[str, Any], rules: Rules, label: str
) -> dict[str, Any]:
    spec = RENDERERS[renderer]
    assert isinstance(spec, JsonSpec)
    if not _nonempty(document):
        return spec.fn(rules, document)
    try:
        extraction = EXTRACTORS[renderer](document)
        roundtrip = spec.fn(extraction.rules, extraction.base)
    except (TypeError, ValueError, AttributeError, KeyError) as error:
        raise LoadoutError(
            f"{label}: native permissions cannot compose with template rules"
        ) from error
    if extraction.notes or (document and _nonempty(roundtrip) != _nonempty(document)):
        raise LoadoutError(
            f"{label}: native permissions do not round-trip losslessly; use an explicit portable permission renderer before selecting template permissions"
        )
    native = extraction.rules
    if renderer in {"opencode", "pi"}:
        default = document.get("permission", {}).get("bash", {}).get("*")
        if default in DECISIONS:
            native = replace(native, default=default)
    return spec.fn(merge_rules(rules, native), extraction.base)


def _nonempty(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple((k, _nonempty(v)) for k, v in value.items() if v not in ({}, [], None))
    if isinstance(value, list):
        return [_nonempty(v) for v in value]
    return value


def _servers_document(agents: tuple[str, ...], servers: dict[str, Server]) -> dict[str, Any]:
    documents = []
    for agent in agents:
        if agent == "codex":
            documents.append(tomllib.loads(render_codex_config(EMPTY_RULES, dict(servers))))
        elif agent == "opencode":
            documents.append({"mcp": render_opencode_servers(servers)})
        else:
            documents.append(render_claude_project_servers(servers))
    if any(document != documents[0] for document in documents[1:]):
        raise LoadoutError(
            "template MCP needs separate artifact routes for agents with different formats"
        )
    return documents[0]


def _codex_policy(document: dict[str, Any], rules: Rules, label: str) -> dict[str, Any]:
    current = document.get("mcp_servers", {})
    if not isinstance(current, dict):
        raise LoadoutError(f"{label}: native mcp_servers is not a server table")
    policies = {}
    for name in codex_mcp_policy(rules):
        block = current.get(name, {})
        if not isinstance(block, dict):
            raise LoadoutError(f"{label}: native MCP server {name!r} is not a table")
        policy = {key: value for key, value in block.items() if key in CODEX_MCP_KEYS}
        enabled = policy.get("enabled", True)
        disabled = policy.get("disabled_tools", [])
        tools = policy.get("tools", {})
        default = policy.get("default_tools_approval_mode", "prompt")
        if (
            not isinstance(enabled, bool)
            or not isinstance(disabled, list)
            or any(not isinstance(tool, str) for tool in disabled)
            or not isinstance(tools, dict)
            or not isinstance(default, str)
            or default not in CODEX_MCP_CATEGORY
            or any(not isinstance(value, dict) for value in tools.values())
        ):
            raise LoadoutError(f"{label}: native MCP policy for {name!r} cannot compose")
        if "tools" in policy:
            policy["tools"] = {
                tool: {"approval_mode": value["approval_mode"]}
                for tool, value in tools.items()
                if "approval_mode" in value
            }
        policies[name] = policy
    try:
        native = extract_codex_mcp(tomlkit.dumps({"mcp_servers": policies}))
    except (TypeError, ValueError) as error:
        raise LoadoutError(f"{label}: native MCP policy cannot compose") from error
    if native.notes:
        raise LoadoutError(f"{label}: native MCP policy cannot compose losslessly")
    rendered = tomllib.loads(render_codex_config(merge_rules(rules, native.rules), {}))
    combined = dict(current)
    for name, policy in rendered.get("mcp_servers", {}).items():
        block = {**current.get(name, {}), **policy}
        if "tools" in policy:
            tools = current.get(name, {}).get("tools", {})
            block["tools"] = {
                **tools,
                **{
                    tool: {**tools.get(tool, {}), **value}
                    for tool, value in policy["tools"].items()
                },
            }
        combined[name] = block
    return {**document, "mcp_servers": combined}


def _document_parts(
    artifacts: Artifacts,
    artifact: Artifact,
    rules: Rules,
    servers: dict[str, Server],
    consumed: dict[str, set[str]],
) -> tuple[dict[str, Any], ...]:
    merged = []
    for part in artifact.parts:
        permissions = part.category in {"permissions", "mcp-permissions"} and rules != EMPTY_RULES
        document = part_document(
            artifacts, artifact, part, rules=rules if permissions and part.renderer else EMPTY_RULES
        )
        if part.category == "mcp" and servers:
            defaults = _servers_document(artifact.agents, servers)
            for key, entries in defaults.items():
                current = document.get(key, {})
                if not isinstance(current, dict):
                    raise LoadoutError(f"{artifact.label}: native {key} is not a server table")
                document = {**document, key: {**entries, **current}}
            _consume(consumed, "mcp", artifact)
        if part.category == "mcp" and "codex" in artifact.agents and _has_mcp(rules):
            if artifact.agents != ("codex",):
                raise LoadoutError(f"{artifact.label}: Codex MCP policy needs a separate route")
            document = _codex_policy(document, rules, artifact.label)
            _consume(consumed, "mcp-permissions", artifact)
        if permissions:
            renderers = tuple(
                dict.fromkeys(
                    part.renderer
                    or (
                        "claude-mcp-permissions"
                        if part.category == "mcp-permissions" and agent == "claude"
                        else "opencode"
                        if agent == "opencode"
                        else f"{agent}-project"
                    )
                    for agent in artifact.agents
                )
            )
            if len(renderers) != 1 or not isinstance(RENDERERS.get(renderers[0]), JsonSpec):
                raise LoadoutError(
                    f"{artifact.label}: template permissions require a compatible permission route"
                )
            if not part.renderer:
                document = _permission_document(renderers[0], document, rules, artifact.label)
            if part.category == "permissions":
                _consume(consumed, "permissions", artifact)
            consumed["mcp-permissions"].update(artifact.agents)
        merged.append(document)
    return tuple(merged)


def _has_mcp(rules: Rules) -> bool:
    return bool(rules.mcp_allow or rules.mcp_ask or rules.mcp_deny)


def _consume(consumed: dict[str, set[str]], category: str, artifact: Artifact) -> None:
    if duplicate := consumed[category].intersection(artifact.agents):
        raise LoadoutError(
            f"template {category} has multiple artifact routes for {', '.join(sorted(duplicate))}; set template_parts = false on the other routes before adding this template"
        )
    consumed[category].update(artifact.agents)


def _skill_outputs(
    artifact: Artifact, source: Path, destination: Path, skills: dict[str, Skill]
) -> dict[Path, Output]:
    if artifact.format != "tree":
        raise LoadoutError(f"{artifact.label}: template skills require a skills tree route")
    if (source / SKILL_DOCUMENT).is_file():
        raise LoadoutError(
            f"{artifact.label}: template skills require a collection route, not an individual skill tree; set template_parts = false here and add a collection route"
        )
    outputs: dict[Path, Output] = {}
    modes = dict(artifact.modes)
    for name, skill in skills.items():
        if (source / name / SKILL_DOCUMENT).is_file():
            continue
        if (source / name).exists() and (
            not (source / name).is_dir() or any((source / name).iterdir())
        ):
            raise LoadoutError(
                f"{artifact.label}: project skill {name!r} has content but no SKILL.md"
            )
        variants = [render_skill(skill, agent) for agent in artifact.agents]
        if any(v != variants[0] for v in variants[1:]):
            raise LoadoutError(
                f"{artifact.label}: skill {name!r} differs between agents; use separate routes"
            )
        mode = modes.get(PurePosixPath(name, SKILL_DOCUMENT))
        outputs[destination / name / SKILL_DOCUMENT] = (
            variants[0] if mode is None else FrozenFile(variants[0].encode(), mode)
        )
        for relative in skill.supporting:
            outputs[destination / name / relative] = Copied(
                skill.document.parent / relative, mode=modes.get(PurePosixPath(name) / relative)
            )
    return outputs


def _text_permissions(artifact: Artifact, source: Path | None, rules: Rules) -> str:
    part = artifact.parts[0]
    renderer = part.renderer or "codex-project"
    spec = RENDERERS[renderer]
    if artifact.agents != ("codex",) or not isinstance(spec, TextSpec):
        raise LoadoutError(
            f"{artifact.label}: template permissions require a portable text renderer"
        )
    if source is None:
        native = EMPTY_RULES
    elif part.renderer:
        native = parse_rules(source)
    elif not source.read_bytes().strip():
        native = EMPTY_RULES
    else:
        raise LoadoutError(
            f"{artifact.label}: convert native permission text to a portable renderer before adding template rules"
        )
    return spec.fn(merge_rules(rules, native))


def render_native_templates(
    root: Path,
    config: ProjectConfig,
    templates: tuple[ResolvedItem, ...],
    *,
    source_inputs: Iterable[Path] = (),
) -> dict[Path, Output]:
    catalogs = load_catalogs(t.path for t in templates)
    prefix = native_template_prefix(config, templates, catalogs=catalogs)
    paths = tuple(t.path for t in templates if t.path in catalogs)
    permissions = contribution_paths(paths, "permissions", catalogs=catalogs)
    parsed_rules = {path: parse_rules(path) for path in dict.fromkeys(permissions)}
    rules = merge_rules(*(parsed_rules[path] for path in permissions))
    mcp = contribution_paths(paths, "mcp", catalogs=catalogs)
    parsed_servers = {path: parse_servers(path) for path in dict.fromkeys(mcp)}
    servers: dict[str, Server] = {}
    for path in mcp:
        servers.update(parsed_servers[path])
    skills = {s.name: s for path in paths for s in catalogs[path].skills()}
    consumed: dict[str, set[str]] = {
        category: set() for category in ("permissions", "mcp", "mcp-permissions", "skills")
    }
    artifacts = config.artifacts
    if artifacts is None:
        return {}
    if not catalogs:
        return render_artifacts(
            artifacts,
            project_root=root,
            source_inputs=source_inputs,
            instruction_prefix=prefix,
        )
    outputs: dict[Path, Output] = {}
    destinations = validate_artifact_paths(
        artifacts, project_root=root, source_inputs=source_inputs
    )
    for artifact, destination in zip(artifacts.records, destinations, strict=True):
        if not artifact.template_parts:
            outputs.update(render_artifact(artifacts, artifact, destination, prefix))
            continue
        if artifact.format in {"json", "toml"}:
            merged = _document_parts(artifacts, artifact, rules, servers, consumed)
            outputs.update(render_document(artifact, destination, merged))
        elif artifact.parts[0].category == "skills" and skills:
            outputs.update(render_artifact(artifacts, artifact, destination, prefix))
            source = artifacts.source_root / artifact.parts[0].source
            outputs.update(_skill_outputs(artifact, source, destination, skills))
            _consume(consumed, "skills", artifact)
        elif artifact.parts[0].category == "permissions" and rules != EMPTY_RULES:
            part = artifact.parts[0]
            permission_source = _source(artifacts.source_root, part.source, optional=part.optional)
            if permission_source is not None:
                _regular_file(permission_source)
            outputs[destination] = _text_permissions(artifact, permission_source, rules)
            _consume(consumed, "permissions", artifact)
        else:
            outputs.update(render_artifact(artifacts, artifact, destination, prefix))
    for category, present in (
        ("permissions", rules != EMPTY_RULES),
        ("mcp", bool(servers)),
        ("mcp-permissions", _has_mcp(rules)),
        ("skills", bool(skills)),
    ):
        missing = set(config.harnesses) - consumed[category]
        if present and missing:
            raise LoadoutError(
                f"template {category} has no compatible artifact route for {', '.join(sorted(missing))}; add an explicit {category} route in artifacts.toml"
            )
    return outputs
