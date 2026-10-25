from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import tomlkit

from .composition import HEADER
from .containment import PROJECT_DIR
from .credentials import credential_material
from .discovery import (
    LINKED_SKILLS,
    PROJECT_ROOTS,
    RUNTIME_DOCUMENTS,
    authored_part,
    path_preconditions,
)
from .emit import PROJECT_SETTINGS_DIR, project_owned_keys, refuse_project_inputs
from .errors import LoadoutError
from .extract import extract, extract_codex_plugins, extract_codex_servers, extract_value
from .harnesses import DIRECTORY_SLICES, GLOBAL_PRESET, KNOWN_HARNESSES, SHARED_SKILL_READERS
from .manifest import MANIFEST_NAME, load_manifest
from .migration_models import (
    Candidate,
    CategoryReadiness,
    ExpectedOutput,
    Inventory,
    Issue,
    MigrationPlan,
    OriginalEntry,
    RootMapping,
    SourceWrite,
    Unadopted,
    UnadoptedKind,
)
from .migration_paths import inventory_layout
from .migration_validation import validate_plan
from .native_documents import key_fingerprints, parse_document
from .output import CATEGORIES, entry_path, sha256
from .output import toml_item as _toml_item
from .permissions.merge import merge_rules
from .permissions.rules import Rules
from .project import PROJECT_CONFIG_NAME, PROJECT_PRESET, load_project_config
from .servers import serialize_servers
from .skills import SKILL_DOCUMENT
from .subagents import NativeAgent, agent_file_name, extract_agent, source_agent

SHARED_SKILL_COPY = (
    "Its harness reads the one .agents/skills copy loadout renders for every harness that "
    "reads that folder, so this copy is retired."
)
CODEX_RULES_OUTPUT = Path(PROJECT_PRESET["codex"]["permissions"].output or "").name
OWNERS: dict[str, dict[str, tuple[str, ...]]] = {
    "claude": {
        "permissions": ("permissions",),
        "hooks": ("hooks",),
        "plugins": ("enabledPlugins", "extraKnownMarketplaces"),
    },
    "codex": {"mcp": ("mcp_servers",), "plugins": ("plugins", "marketplaces")},
    "droid": {
        "permissions": ("commandAllowlist", "commandDenylist", "commandBlocklist"),
        "plugins": ("enabledPlugins", "extraKnownMarketplaces"),
    },
    "opencode": {"permissions": ("permission",), "mcp": ("mcp",), "plugins": ("plugin",)},
    "pi": {"plugins": ("packages",)},
}
NOT_ADOPTED = "No source slice adopts this category; existing files stay in place."
# Templates carry no agents, so agents is a readiness category but not an output.CATEGORIES one.
READINESS_CATEGORIES = CATEGORIES | {"agents"}
TREE_CATEGORIES = {
    "skills": "skills",
    "commands": "instructions",
    "agents": "instructions",
    "rules": "instructions",
    "hooks": "hooks",
    "plugins": "plugins",
    "plugin": "plugins",
    "scripts": "support",
}


PROJECT_INSTRUCTION = "instructions/main.md"
SERVERS_EXAMPLE = b"""\
# MCP server definitions, rendered for every agent that reads MCP config.
# Uncomment a block, edit it, then run `loadout sync --global`.
#
# [context7]
# transport = "stdio"
# command = "npx"
# args = ["-y", "@upstash/context7-mcp"]
#
# [jina]
# transport = "http"
# url = "https://mcp.jina.ai/v1"
# auth_env_var = "JINA_API_KEY"   # the NAME of an environment variable, never a token
"""

SOURCE_README = b"""\
# Agent configuration

A loadout global source. `loadout sync --global` renders it into each agent's own
configuration; edit here rather than in the generated files.

## Layout

    loadout.toml       the agents, and the slices each one takes
    permissions.toml   shell and MCP tool rules for every agent
    mcp.toml           MCP server definitions for every agent that reads them
    instructions/      instruction fragments, named in reading order
    settings/          settings fragments, one JSON document per name
    hooks/             hook fragments, one JSON document per name
    plugins/           plugin fragments, one JSON document per name
    skills/            skills, one file or directory each
    module-config/     files copied verbatim into an agent's config directory

Only what you use has to exist.

## Add hooks, plugins or settings

1. Write the fragment, for example `hooks/claude.json`.
2. Add the category to `use` in the `[[source]]` block of `loadout.toml`.
3. Name the fragment in the agent's block, for example `hooks = ["claude"]`.
4. Run `loadout sync --global`.

Hooks, plugins, settings and instructions must be named: an agent block that leaves
one out means loadout does not manage it. Permissions, MCP servers, skills and
module-config apply to every declared agent without being named.
"""


def serialize_document(document: dict[str, Any], format_name: str) -> bytes:
    if format_name == "json":
        return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
    result = tomlkit.document()
    for key, value in document.items():
        result.add(key, _toml_item(value))
    return tomlkit.dumps(result).encode()


def _rules_source(rules: Rules) -> bytes:
    shell: dict[str, Any] = {key: list(rules.shell(key)) for key in ("allow", "ask", "deny")}
    if rules.default is not None:
        shell["default"] = rules.default
    return tomlkit.dumps(
        {
            "shell": shell,
            "mcp": {key: list(rules.mcp(key)) for key in ("allow", "ask", "deny")},
            "claude": {
                "extra": {
                    "allow": list(rules.claude_extra_allow),
                    "deny": list(rules.claude_extra_deny),
                }
            },
            "opencode": {"extra": dict(rules.opencode_extra)},
        }
    ).encode()


def _merged_rules(values: list[tuple[str, object]]) -> Rules:
    return merge_rules(*(value for _, value in values if isinstance(value, Rules)))


def _merged_servers(values: list[tuple[str, object]]) -> dict[str, Any]:
    servers: dict[str, Any] = {}
    for _, value in values:
        assert isinstance(value, dict)
        for name, server in value.items():
            if servers.setdefault(name, server) != server:
                raise LoadoutError(
                    f"MCP server '{name}' is defined differently across harnesses; "
                    "make the definitions agree, then run init again"
                )
    return servers


def _partial(candidate: Candidate, scope: str | None) -> bool:
    """A global document a harness also writes runtime state into."""
    return (
        candidate.format in {"json", "toml"}
        and scope == "global"
        and RUNTIME_DOCUMENTS.get(candidate.harnesses[0]) == candidate.document_name
    )


def _authored(candidate: Candidate, scope: str | None) -> Candidate:
    if candidate.format not in {"json", "toml"} or not _partial(candidate, scope):
        return candidate
    document = parse_document(candidate.content.decode(), candidate.format)
    content = serialize_document(authored_part(document, candidate.harnesses[0]), candidate.format)
    return replace(candidate, content=content)


def _choose(inventory: Inventory) -> tuple[tuple[Candidate, ...], tuple[Issue, ...]]:
    grouped: dict[Path, list[Candidate]] = defaultdict(list)
    issues: list[Issue] = []
    for candidate in inventory.candidates:
        if candidate.disposition != "migrated" or candidate.destination is None:
            continue
        try:
            grouped[candidate.destination].append(_authored(candidate, inventory.scope))
        except (LoadoutError, UnicodeError, ValueError, TypeError):
            issues.append(
                Issue(
                    "invalid-native-document",
                    (candidate.path,),
                    "Native document could not be parsed safely; repair it, then run init again.",
                )
            )
    chosen = []
    for destination, candidates in grouped.items():
        # Every original is its own destination, so a group only ever repeats one file.
        if len({(c.content, c.mode) for c in candidates}) > 1:
            raise LoadoutError(f"two different originals claim one destination: {destination}")
        selected = candidates[0]
        chosen.append(
            replace(
                selected,
                harnesses=tuple(dict.fromkeys(a for c in candidates for a in c.harnesses)),
                private=any(c.private for c in candidates),
            )
        )
    return tuple(chosen), tuple(issues)


def _destination_template(inventory: Inventory, destination: Path) -> str:
    if inventory.scope == "project" and destination.is_relative_to(inventory.root):
        return destination.relative_to(inventory.root).as_posix()
    for mapping in sorted(inventory.mappings, key=lambda m: len(m.destination.parts), reverse=True):
        if mapping.destination_template is None:
            continue
        if mapping.kind == "file" and mapping.destination == destination:
            return mapping.destination_template
        if mapping.kind == "file" or not destination.is_relative_to(mapping.destination):
            continue
        return f"{mapping.destination_template}/{destination.relative_to(mapping.destination).as_posix()}"
    return str(destination)


def _preset_destination(inventory: Inventory, template: str) -> Path:
    for mapping in inventory.mappings:
        prefix = mapping.destination_template
        if prefix is None:
            continue
        if template == prefix:
            return mapping.destination
        if template.startswith(prefix + "/"):
            return mapping.destination / template.removeprefix(prefix + "/")
    raise LoadoutError(f"no discovered root resolves preset destination {template!r}")


def _preset_routes(inventory: Inventory, config: dict[str, Any]) -> tuple[tuple[str, Path], ...]:
    return tuple(
        (spec.destination, _preset_destination(inventory, spec.destination))
        for agent in inventory.harnesses
        for category, spec in GLOBAL_PRESET[agent].items()
        if spec.destination is not None and category in config.get(agent, {})
    )


CODEX_RULES_ABSORBED = (
    "Its rules are adopted into the source, and Codex reads every file in this folder."
)
JSONC_ABSORBED = "Its settings are adopted into opencode.json, and OpenCode would read both files."


class _PlanBuilder:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.available_categories: set[tuple[str, str]] = set()
        self.records: dict[Path, dict[str, Any]] = {}
        self.writes: dict[Path, SourceWrite] = {}
        self.expected: dict[Path, ExpectedOutput] = {}
        self.skill_policies: dict[str, dict[str, Any]] = {}
        self.private_skill_names: set[str] = set()
        self.issues: list[Issue] = []
        self.notes: list[str] = []
        self.selected: tuple[Candidate, ...] = ()
        self.project_values: dict[str, list[tuple[str, object]]] = defaultdict(list)
        self.project_instructions: list[Candidate] = []
        self.global_values: dict[str, list[tuple[str, object]]] = defaultdict(list)
        self.global_instructions: dict[str, Candidate] = {}
        self.global_slices: dict[str, set[str]] = defaultdict(set)
        self.retained: set[Path] = set()
        self.unadopted: list[Unadopted] = []
        self.unadopted_paths: set[Path] = set()
        self.absorbed: dict[Path, str] = {}
        # (agent, category) values adopted from an original only its owner could read.
        self.restricted: set[tuple[str, str]] = set()

    def adopt_value(
        self,
        scope: dict[str, list[tuple[str, object]]],
        category: str,
        agent: str,
        value: object,
        candidate: Candidate,
    ) -> None:
        scope[category].append((agent, value))
        if _owner_only(candidate.mode):
            self.restricted.add((agent, category))

    def restricted_values(self, category: str, values: list[tuple[str, object]]) -> bool:
        return any((agent, category) in self.restricted for agent, _ in values)

    def leave_out(self, kind: UnadoptedKind, name: str, candidates: list[Candidate]) -> None:
        """Adopt nothing of an item whose copies disagree, and name every copy."""
        originals: dict[Path, tuple[str, ...]] = {}
        for candidate in candidates:
            item = _item_path(kind, name, candidate)
            originals[item] = tuple(dict.fromkeys((*originals.get(item, ()), *candidate.harnesses)))
            self.unadopted_paths.update(_original_paths(candidate))
            if candidate.destination is not None:
                self.records.pop(candidate.destination, None)
        self.unadopted.append(Unadopted(kind, name, tuple(sorted(originals.items()))))

    def write(
        self,
        relative: Path,
        content: bytes,
        *,
        mode: int = 0o644,
        private: bool = False,
        restricted: bool = False,
    ) -> None:
        path = self.inventory.source_root / relative
        mode = _source_mode(mode, content, path, restricted=restricted)
        item = SourceWrite(path, content, mode, private)
        previous = self.writes.get(path)
        if previous is not None and previous != item:
            raise LoadoutError(
                f"two originals would write different {relative.as_posix()}; make them "
                "identical or remove one, then run init again"
            )
        self.writes[path] = item

    def record(
        self, destination: Path, agents: tuple[str, ...], format_name: str
    ) -> dict[str, Any]:
        key = "output" if self.inventory.scope == "project" else "destination"
        value = (
            destination.relative_to(self.inventory.root).as_posix()
            if key == "output"
            else _destination_template(self.inventory, destination)
        )
        return {"harnesses": list(agents), key: value, "format": format_name}

    def expect(self, candidate: Candidate) -> None:
        assert candidate.destination is not None
        content = candidate.content
        if candidate.format in {"json", "toml"}:
            keys = frozenset(parse_document(content.decode(), candidate.format))
            self.expected[candidate.destination] = ExpectedOutput(
                candidate.destination,
                candidate.format,
                sha256(content),
                mode=0o600,
                fingerprints=key_fingerprints(content.decode(), candidate.format, keys),
            )
        else:
            # A copy deploys with its source's mode, which a secret restricts.
            mode = _source_mode(candidate.mode, content, candidate.destination)
            self.expected[candidate.destination] = ExpectedOutput(
                candidate.destination, "copy", sha256(content), mode
            )

    def document(self, candidate: Candidate, *, existing: bool = True) -> None:
        if candidate.path.suffix == ".jsonc":
            self.adopt_jsonc(candidate)
        if self.inventory.scope == "project":
            self.project_document(candidate, existing=existing)
            return
        self.global_document(candidate, existing=existing)
        return

    def adopt_jsonc(self, candidate: Candidate) -> None:
        """OpenCode reads opencode.jsonc beside opencode.json, so the converted document
        replaces it."""
        self.notes.append(f"{candidate.path}: comments in {candidate.path.name} will be lost")
        self.absorbed.update(dict.fromkeys(_original_paths(candidate), JSONC_ABSORBED))

    def global_document(self, candidate: Candidate, *, existing: bool) -> None:
        assert candidate.destination is not None
        document = parse_document(candidate.content.decode(), candidate.format)
        agent = candidate.harnesses[0]
        special = candidate.category in {"mcp", "hooks", "permissions", "mcp-permissions"}
        composite = candidate.document_name in {
            "settings.json",
            "settings.local.json",
            "config.toml",
            "opencode.json",
        }
        ownership = (
            {candidate.category: tuple(document)}
            if special
            else OWNERS.get(agent, {})
            if composite
            else {}
        )
        if special and not document:
            defaults = {
                "mcp": ("mcpServers",),
                "hooks": ("hooks",),
                "mcp-permissions": ("allow", "ask", "deny"),
                "permissions": ("$schema", "permission"),
            }
            ownership = {candidate.category: defaults[candidate.category]}
        claimed = {key for keys in ownership.values() for key in keys}
        values = {
            category: {key: value for key, value in document.items() if key in keys}
            for category, keys in ownership.items()
        }
        residual = {key: value for key, value in document.items() if key not in claimed}
        if not special or residual:
            values = {"settings": residual, **values}
        self.available_categories.update(
            (agent, category) for agent in candidate.harnesses for category in values
        )
        for category, value in values.items():
            if category in {"permissions", "mcp-permissions"}:
                renderer = _permission_renderer(candidate, False)
                if renderer is not None:
                    self.adopt_value(
                        self.global_values,
                        "permissions",
                        agent,
                        self.extracted_rules(renderer, candidate, document, existing=existing),
                        candidate,
                    )
                    self.global_slices[agent].add("permissions")
                continue
            if category in {"hooks", "plugins", "mcp"}:
                spec = GLOBAL_PRESET[agent].get(category)
                if spec is not None and spec.renderer:
                    extraction = (
                        extract_codex_servers(candidate.content.decode())
                        if spec.renderer == "codex-config"
                        else extract_codex_plugins(candidate.content.decode())
                        if spec.renderer == "codex-plugins"
                        else extract_value(spec.renderer, document)
                    )
                    extracted = extraction.value
                    if existing:
                        self.notes.extend(
                            f"{candidate.destination}: {note.detail}" for note in extraction.notes
                        )
                    if existing or extracted:
                        self.adopt_value(self.global_values, category, agent, extracted, candidate)
                        self.global_slices[agent].add(category)
                continue
            if category == "settings" and (existing or value):
                self.adopt_value(self.global_values, "settings", agent, value, candidate)
                self.global_slices[agent].add("settings")
        if existing:
            self.records[candidate.destination] = self.record(
                candidate.destination, candidate.harnesses, candidate.format
            )

    def project_document(self, candidate: Candidate, *, existing: bool) -> None:
        assert candidate.destination is not None
        document = parse_document(candidate.content.decode(), candidate.format)
        matched = False
        for agent in candidate.harnesses:
            for category, spec in PROJECT_PRESET[agent].items():
                if (
                    spec.output is None
                    or self.inventory.root / spec.output != candidate.destination
                ):
                    continue
                if category == "permissions":
                    rules = self.extracted_rules(
                        spec.renderer or "", candidate, document, existing=existing
                    )
                    self.adopt_value(self.project_values, category, agent, rules, candidate)
                elif category in {"hooks", "plugins", "mcp"}:
                    extraction = extract_value(spec.renderer or "", document)
                    value = extraction.value
                    if existing:
                        self.notes.extend(
                            f"{candidate.destination}: {note.detail}" for note in extraction.notes
                        )
                    if not existing and not value:
                        continue
                    self.adopt_value(self.project_values, category, agent, value, candidate)
                else:
                    continue
                matched = True
        if matched and existing:
            self.project_settings(candidate, document)
            self.records[candidate.destination] = self.record(
                candidate.destination, candidate.harnesses, candidate.format
            )
        elif not matched and existing:
            self.retained.update(_original_paths(candidate))

    def project_settings(self, candidate: Candidate, document: dict[str, Any]) -> None:
        """A co-owned file's keys that no slice writes go into the project source, so a
        clone renders them too rather than finding them only on this machine (ADR 0030)."""
        for agent in candidate.harnesses:
            spec = PROJECT_PRESET[agent]["permissions"]
            if not spec.preserve_foreign or self.inventory.root / str(spec.output) != (
                candidate.destination
            ):
                continue
            owned = project_owned_keys(self.inventory.harnesses, str(spec.output))
            residual = {key: value for key, value in document.items() if key not in owned}
            if residual:
                self.write(
                    Path(PROJECT_SETTINGS_DIR) / f"{agent}.json",
                    serialize_document(residual, "json"),
                    private=candidate.private,
                    restricted=_owner_only(candidate.mode),
                )

    def opaque(self, candidate: Candidate, *, existing: bool = True) -> None:
        if self.inventory.scope == "project":
            self.project_opaque(candidate)
            return
        self.global_opaque(candidate, existing=existing)
        return

    def global_opaque(self, candidate: Candidate, *, existing: bool) -> None:
        assert candidate.destination is not None
        agent = candidate.harnesses[0]
        if candidate.category == "instructions":
            if existing:
                self.global_instructions[agent] = candidate
                self.global_slices[agent].add("instructions")
                self.records[candidate.destination] = self.record(
                    candidate.destination, candidate.harnesses, candidate.format
                )
            return
        if candidate.category == "permissions":
            renderer = _permission_renderer(candidate, False)
            if renderer is not None and candidate.content:
                rules = self.extracted_rules(renderer, candidate, candidate.content.decode())
                self.adopt_value(self.global_values, "permissions", agent, rules, candidate)
                self.global_slices[agent].add("permissions")
                if existing:
                    self.adopt_rules_file(candidate)
            return
        if existing:
            self.retained.update(_original_paths(candidate))

    def project_opaque(self, candidate: Candidate) -> None:
        assert candidate.destination is not None
        if candidate.category == "instructions" and candidate.destination in {
            self.inventory.root / spec.output
            for agent in candidate.harnesses
            if (spec := PROJECT_PRESET[agent].get("instructions")) is not None
            and spec.output is not None
        }:
            self.project_instructions.append(candidate)
            self.records[candidate.destination] = self.record(
                candidate.destination, candidate.harnesses, candidate.format
            )
            return
        if candidate.category == "permissions":
            renderer = _permission_renderer(candidate, True)
            if renderer is not None:
                rules = self.extracted_rules(renderer, candidate, candidate.content.decode())
                agent = candidate.harnesses[0]
                self.adopt_value(self.project_values, "permissions", agent, rules, candidate)
                self.adopt_rules_file(candidate)
                return
        self.retained.update(_original_paths(candidate))

    def adopt_rules_file(self, candidate: Candidate) -> None:
        """Codex reads its whole rules folder, so a rules file loadout does not render,
        such as the approvals Codex writes to default.rules, is deleted once its rules
        are in the source rather than left enforcing beside it (ADR 0030)."""
        assert candidate.destination is not None
        if candidate.harnesses[0] == "codex" and candidate.destination.name != CODEX_RULES_OUTPUT:
            self.absorbed.update(dict.fromkeys(_original_paths(candidate), CODEX_RULES_ABSORBED))
            return
        self.records[candidate.destination] = self.record(
            candidate.destination, candidate.harnesses, candidate.format
        )

    def extracted_rules(
        self, renderer: str, candidate: Candidate, document: Any, *, existing: bool = True
    ) -> Rules:
        try:
            extracted = extract(renderer, document)
        except LoadoutError as error:
            raise LoadoutError(f"{candidate.destination}: {error}") from None
        if existing:
            self.notes.extend(f"{candidate.destination}: {note.detail}" for note in extracted.notes)
        return extracted.rules

    def write_project_sources(self) -> None:
        documents = [c for c in self.project_instructions if not _imports_agents_md(c)]
        documents = documents or self.project_instructions
        if documents and len({candidate.content for candidate in documents}) != 1:
            self.leave_out("instructions", "main", self.project_instructions)
        elif documents:
            candidate = documents[0]
            self.write(Path(PROJECT_INSTRUCTION), candidate.content, mode=candidate.mode)
        for category, values in self.project_values.items():
            match category:
                case "permissions":
                    self._write_permissions(values)
                case "mcp":
                    self._write_servers(values)
                case _:
                    if len({repr(value) for _, value in values}) != 1:
                        raise LoadoutError(
                            f"project {category} documents disagree across harnesses"
                        )
                    value = values[0][1]
                    assert isinstance(value, dict)
                    self.write(
                        Path(f"{category}.json"),
                        serialize_document(value, "json"),
                        restricted=self.restricted_values(category, values),
                    )

    def project_instruction_order(self) -> tuple[str, ...]:
        """The instruction order the project config names: `main`, once it is written."""
        written = self.inventory.source_root / PROJECT_INSTRUCTION in self.writes
        return ("main",) if written else ()

    def write_global_sources(self) -> None:
        for agent, candidate in self.global_instructions.items():
            self.write(Path(f"instructions/{agent}.md"), candidate.content, mode=candidate.mode)
        for category, values in self.global_values.items():
            match category:
                case "permissions":
                    self._write_permissions(values)
                case "mcp":
                    self._write_servers(values, fallback=SERVERS_EXAMPLE)
                case _:
                    for agent, value in values:
                        assert isinstance(value, dict)
                        self.write(
                            Path(f"{category}/{agent}.json"),
                            serialize_document(value, "json"),
                            restricted=(agent, category) in self.restricted,
                        )
        if self.inventory.source_root / "mcp.toml" not in self.writes:
            self.write(Path("mcp.toml"), SERVERS_EXAMPLE)
        self.write(Path("README.md"), SOURCE_README)

    def _write_permissions(self, values: list[tuple[str, object]]) -> None:
        self.write(
            Path("permissions.toml"),
            _rules_source(_merged_rules(values)),
            restricted=self.restricted_values("permissions", values),
        )

    def _write_servers(self, values: list[tuple[str, object]], fallback: bytes = b"") -> None:
        self.write(
            Path("mcp.toml"),
            serialize_servers(_merged_servers(values)) or fallback,
            restricted=self.restricted_values("mcp", values),
        )

    def global_blocks(self) -> dict[str, dict[str, object]]:
        """Each agent's manifest block: the slices `write_global_sources` wrote for it."""
        blocks: dict[str, dict[str, object]] = defaultdict(dict)
        for agent in self.global_instructions:
            blocks[agent]["instructions"] = [agent]
        for category, values in self.global_values.items():
            for agent, _ in values:
                blocks[agent][category] = True if category in {"permissions", "mcp"} else [agent]
        for agent, slices in self.global_slices.items():
            for directory in (*DIRECTORY_SLICES, "module-config"):
                if directory in slices:
                    blocks[agent][directory] = True
        return blocks

    def catalog_skill_tree(
        self,
        destination: Path,
        agents: tuple[str, ...],
        candidates: tuple[Candidate, ...],
    ) -> None:
        if not candidates:
            return
        assert candidates[0].destination is not None
        relative = candidates[0].destination.relative_to(destination)
        name = relative.parts[0]
        if any(
            candidate.destination is None
            or candidate.destination.relative_to(destination).parts[0] != name
            for candidate in candidates
        ):
            raise LoadoutError("canonical skill migration requires one named skill tree")
        private = any(candidate.private for candidate in candidates)
        if private:
            self.private_skill_names.add(name)
        suffix = ".local" if private else ""
        document = next(
            (
                candidate
                for candidate in candidates
                if candidate.destination == destination / name / "SKILL.md"
            ),
            None,
        )
        if document is None:
            raise LoadoutError("canonical skill migration requires SKILL.md")
        source_support = Path("skills") / f"{name}{suffix}"
        source_document = source_support / "SKILL.md"
        self.write(
            source_document,
            document.content,
            mode=document.mode,
            private=private,
        )
        self.expect(document)
        for candidate in candidates:
            if candidate is document:
                continue
            assert candidate.destination is not None
            relative_support = candidate.destination.relative_to(destination / name)
            self.write(
                source_support / relative_support,
                candidate.content,
                mode=candidate.mode,
                private=private,
            )
            self.expect(candidate)
        policy = self.skill_policies.setdefault(name, {"agents": [], "render": False})
        policy["agents"] = list(dict.fromkeys((*policy["agents"], *agents)))
        if self.inventory.scope == "project":
            self.project_skill_route(destination / name, agents)
        else:
            for agent in agents:
                self.global_slices[agent].add("skills")

    def originals(self, candidates: tuple[Candidate, ...]) -> None:
        self.selected = candidates
        remaining = {c.destination: c for c in candidates}
        for mapping in self.inventory.mappings:
            self.original_mapping(mapping, remaining)
        for candidate in remaining.values():
            if candidate.format in {"json", "toml"}:
                self.document(candidate)
            else:
                self.opaque(candidate)

    def original_mapping(
        self, mapping: RootMapping, remaining: dict[Path | None, Candidate]
    ) -> None:
        if mapping.kind == "file":
            return
        for directory, category in TREE_CATEGORIES.items():
            if directory == "rules" and "codex" in mapping.harnesses:
                continue
            target = mapping.destination / directory
            entries = tuple(
                c for p, c in remaining.items() if p is not None and p.is_relative_to(target)
            )
            if not entries or any(c.format != "copy" for c in entries):
                continue
            if not self.handle_global_tree(mapping, directory, category, entries) and not (
                self.handle_project_tree(category, entries)
            ):
                agents = tuple(dict.fromkeys(a for c in entries for a in c.harnesses))
                self.original_tree(target, agents, entries)
            for candidate in entries:
                remaining.pop(candidate.destination)

    def handle_global_tree(
        self,
        mapping: RootMapping,
        directory: str,
        category: str,
        entries: tuple[Candidate, ...],
    ) -> bool:
        if self.inventory.scope != "global" or category == "skills":
            return False
        if directory in {"commands", "agents"}:
            self.retained.update(
                path for candidate in entries for path in _original_paths(candidate)
            )
        else:
            self.global_tree(mapping, entries)
        return True

    def handle_project_tree(self, category: str, entries: tuple[Candidate, ...]) -> bool:
        if self.inventory.scope != "project" or category not in {
            "instructions",
            "hooks",
            "plugins",
            "support",
            "module-config",
        }:
            return False
        self.retained.update(path for candidate in entries for path in _original_paths(candidate))
        return True

    def global_tree(self, mapping: RootMapping, entries: tuple[Candidate, ...]) -> None:
        supported = tuple(
            agent for agent in mapping.harnesses if "module-config" in GLOBAL_PRESET[agent]
        )
        if not supported:
            self.retained.update(
                path for candidate in entries for path in _original_paths(candidate)
            )
            return
        for agent in supported:
            for candidate in entries:
                assert candidate.destination is not None
                relative = candidate.destination.relative_to(mapping.destination)
                self.write(
                    Path("module-config") / agent / relative,
                    candidate.content,
                    mode=candidate.mode,
                    private=candidate.private,
                )
            self.global_slices[agent].add("module-config")

    def original_tree(
        self, target: Path, agents: tuple[str, ...], entries: tuple[Candidate, ...]
    ) -> None:
        by_name: dict[str, list[Candidate]] = {}
        for candidate in entries:
            assert candidate.destination is not None
            relative = candidate.destination.relative_to(target)
            by_name.setdefault(relative.parts[0], []).append(candidate)
        for grouped in by_name.values():
            candidates = tuple(grouped)
            if any(
                candidate.destination is not None and candidate.destination.name == SKILL_DOCUMENT
                for candidate in candidates
            ):
                self.catalog_skill_tree(target, agents, candidates)
                continue
            # No skill: a loose file or a folder without SKILL.md stays where it is.
            self.retained.update(path for c in candidates for path in _original_paths(c))

    def dormant(
        self, destination: Path, agents: tuple[str, ...], category: str, format_name: str = "copy"
    ) -> None:
        self.available_categories.update((agent, category) for agent in agents)
        if any(
            destination.is_relative_to(p) or p.is_relative_to(destination) for p in self.records
        ):
            return
        if format_name == "tree" or (
            self.inventory.scope == "project" and format_name not in {"json", "toml"}
        ):
            return
        candidate = Candidate(
            destination,
            None,
            destination,
            agents,
            category,
            "migrated",
            "Dormant category route.",
            b"{}\n" if format_name == "json" else b"",
            format=format_name,
        )
        if format_name in {"json", "toml"}:
            self.document(candidate, existing=False)
        else:
            self.opaque(candidate, existing=False)

    def linked_skill(self, link: Candidate) -> None:
        """A skill folder linking to the same skill in another skills folder: its readers
        get the skill adopted from there, rendered where the link was."""
        assert link.destination is not None
        policy = self.skill_policies.get(link.path.name)
        if policy is None:
            raise LoadoutError(
                f"{link.path} links to a skill init leaves out, so retiring the link would "
                "drop it; merge the skill's copies with /loadout, then run init again"
            )
        policy["agents"] = list(dict.fromkeys((*policy["agents"], *link.harnesses)))
        self.project_skill_route(link.destination, link.harnesses)

    def shared_skill_copies(self) -> None:
        """With Codex, the emitter writes one `.agents/skills` copy for every reader of that
        folder whose copy would be the same bytes, which an adopted skill's always are, since
        init adopts it unrendered. A reader's own copy is then retired rather than expected."""
        if self.inventory.scope != "project":
            return
        for name, policy in self.skill_policies.items():
            agents = set(policy["agents"])
            readers = agents.intersection(SHARED_SKILL_READERS) - {"codex"}
            if "codex" not in agents:
                continue
            for reader in sorted(readers):
                folder = self.inventory.root / str(PROJECT_PRESET[reader]["skills"].output) / name
                self.records.pop(folder, None)
                for path in [p for p in self.expected if p.is_relative_to(folder)]:
                    del self.expected[path]
                self.absorbed.update(
                    (path, SHARED_SKILL_COPY)
                    for candidate in self.inventory.candidates
                    if candidate.destination is not None
                    and candidate.destination.is_relative_to(folder)
                    for path in _original_paths(candidate)
                )

    def project_skill_route(self, destination: Path, agents: tuple[str, ...]) -> None:
        record = self.record(destination, agents, "tree")
        record.update(category="skills")
        self.records[destination] = record

    def dormant_routes(self) -> None:
        project = self.inventory.scope == "project"
        for agent in self.inventory.harnesses:
            root = (
                self.inventory.root / PROJECT_ROOTS[agent]
                if project
                else next(
                    m.destination
                    for m in self.inventory.mappings
                    if m.kind == "harness" and m.harnesses == (agent,)
                )
            )
            settings = (
                self.inventory.root / "opencode.json"
                if project and agent == "opencode"
                else root
                / (
                    "config.toml"
                    if agent == "codex"
                    else "opencode.json"
                    if agent == "opencode"
                    else "settings.json"
                )
            )
            self.dormant(settings, (agent,), "settings", "toml" if agent == "codex" else "json")
            instruction = (
                self.inventory.root / ("CLAUDE.md" if agent == "claude" else "AGENTS.md")
                if project
                else root / ("CLAUDE.md" if agent == "claude" else "AGENTS.md")
            )
            consumers = (
                tuple(a for a in self.inventory.harnesses if a != "claude")
                if project and agent != "claude"
                else (agent,)
            )
            self.dormant(instruction, consumers, "instructions")
            self.available_categories.add((agent, "skills"))
            if not project:
                self.global_slices[agent].update(DIRECTORY_SLICES)
            if agent == "claude":
                self.dormant(root / "hooks", (agent,), "hooks", "tree")
                if not project:
                    self.dormant(root / "commands", (agent,), "instructions", "tree")
                self.dormant(root / "mcp-permissions.json", (agent,), "mcp-permissions", "json")
                mcp = (
                    self.inventory.root / ".mcp.json"
                    if project
                    else next(
                        m.destination
                        for m in self.inventory.mappings
                        if m.kind == "file" and m.destination.name == ".claude.json"
                    )
                )
                self.dormant(
                    mcp,
                    tuple(a for a in ("claude", "pi") if a in self.inventory.harnesses)
                    if project
                    else (agent,),
                    "mcp",
                    "json",
                )
            elif agent == "codex":
                self.dormant(root / "rules/permissions.rules", (agent,), "permissions")
                if not project:
                    self.dormant(root / "hooks.json", (agent,), "hooks", "json")
                    self.available_categories.add((agent, "plugins"))
            elif agent == "opencode":
                self.available_categories.add((agent, "hooks"))
                self.dormant(root / "plugins", (agent,), "plugins", "tree")
            elif agent == "pi":
                self.dormant(
                    root / "extensions/pi-permission-system/config.json",
                    (agent,),
                    "permissions",
                    "json",
                )
                self.available_categories.add((agent, "hooks"))
                self.dormant(
                    self.inventory.root / ".mcp.json" if project else root / "mcp.json",
                    (agent,),
                    "mcp",
                    "json",
                )


def _permission_renderer(candidate: Candidate, project: bool) -> str | None:
    """The renderer of the slice this permissions document belongs to, from the preset,
    so a harness the preset gains is never silently skipped. Codex's permissions are its
    rules files; its config.toml belongs to other slices."""
    agent = candidate.harnesses[0]
    if agent == "codex" and candidate.path.suffix != ".rules":
        return None
    category = "mcp-permissions" if candidate.category == "mcp-permissions" else "permissions"
    spec = (PROJECT_PRESET if project else GLOBAL_PRESET)[agent].get(category)
    return spec.renderer if spec is not None else None


def _initialized(inventory: Inventory) -> MigrationPlan:
    assert inventory.initialized is not None
    config_path = inventory.initialized
    if inventory.scope == "project":
        # What sync refuses to read is no source adopted as it is (review 4, batch 2.7).
        try:
            refuse_project_inputs(inventory.root)
        except LoadoutError as error:
            return MigrationPlan(
                inventory,
                issues=(Issue("initialized-source", (inventory.root / PROJECT_DIR,), str(error)),),
            )
    try:
        if inventory.scope == "project":
            config = load_project_config(config_path)
            if inventory.harnesses and set(inventory.harnesses) != set(config.harnesses):
                raise LoadoutError("configured harness selection differs")
            inventory = replace(inventory, harnesses=config.harnesses)
        else:
            manifest = load_manifest(config_path)
            agents = tuple(
                dict.fromkeys(
                    (
                        *(p.harness for p in manifest.permissions if p.harness),
                        *(
                            p.renderer.split("-", 1)[0]
                            for p in manifest.permissions
                            if p.renderer.split("-", 1)[0] in KNOWN_HARNESSES
                        ),
                        *(t.name for t in manifest.targets if t.name in KNOWN_HARNESSES),
                        *(s.harness for s in manifest.skills),
                        *(m.harness for m in manifest.module_config),
                    )
                )
            )
            if inventory.harnesses and set(inventory.harnesses) != set(agents):
                raise LoadoutError("configured harness selection differs")
            inventory = replace(inventory, harnesses=agents)
    except (LoadoutError, OSError, ValueError):
        return MigrationPlan(
            inventory,
            issues=(
                Issue(
                    "initialized-source",
                    (config_path,),
                    "An existing loadout source needs repair or a matching harness selection; it will not be reinterpreted as raw configuration.",
                ),
            ),
        )
    return MigrationPlan(
        inventory,
        preconditions=inventory.preconditions,
        already_initialized=True,
        notes=(
            "Existing loadout source recognized. No migration is planned; use normal check/sync for its managed scope.",
        ),
    )


def _project_config(inventory: Inventory, builder: _PlanBuilder) -> dict[str, Any]:
    config: dict[str, Any] = {"harnesses": list(inventory.harnesses)}
    builder.write_project_sources()
    if instructions := builder.project_instruction_order():
        config["instructions"] = list(instructions)
    return config


def _global_config(inventory: Inventory, builder: _PlanBuilder) -> dict[str, Any]:
    builder.write_global_sources()
    blocks = builder.global_blocks()
    offered = {
        relative.parts[0].removesuffix(".toml").removesuffix(".json")
        for path in builder.writes
        if (relative := path.relative_to(inventory.source_root)).parts
    }
    use = [
        category
        for category in (
            "instructions",
            "permissions",
            "mcp",
            "settings",
            "hooks",
            "plugins",
            "skills",
            "agents",
            "module-config",
        )
        if category in offered
        or category == "agents"
        or any(category in slices for slices in builder.global_slices.values())
    ]
    config: dict[str, Any] = {
        "source": [{"name": "migrated", "path": ".", "use": use}],
    }
    for agent in inventory.harnesses:
        config[agent] = blocks.get(agent, {})
    return config


def plan_migration(inventory: Inventory) -> MigrationPlan:
    if inventory.initialized is not None:
        return _initialized(inventory)
    candidates, conflicts = _choose(inventory)
    issues = (*inventory.issues, *conflicts)
    if issues:
        return MigrationPlan(inventory, preconditions=inventory.preconditions, issues=issues)
    builder = _PlanBuilder(inventory)
    builder.absorbed.update(
        (c.path, c.reason) for c in inventory.candidates if c.category == LINKED_SKILLS
    )
    for name, conflicting in _conflicting_skills(inventory, candidates).items():
        builder.leave_out("skill", name, conflicting)
    try:
        _adopt_agents(builder, candidates)
        candidates = tuple(
            c
            for c in candidates
            if c.category != "agents" and not builder.unadopted_paths & {c.path}
        )
        builder.originals(candidates)
        for link in inventory.candidates:
            if link.category == LINKED_SKILLS and link.canonical is None:
                builder.linked_skill(link)
        builder.shared_skill_copies()
        builder.dormant_routes()
    except (LoadoutError, UnicodeError, ValueError, TypeError) as error:
        return MigrationPlan(
            inventory,
            issues=(
                Issue(
                    "source-composition",
                    (inventory.source_root,),
                    f"The native sources could not be composed safely: {error}",
                ),
            ),
        )
    config = (
        _project_config(inventory, builder)
        if inventory.scope == "project"
        else _global_config(inventory, builder)
    )
    preset_routes = _preset_routes(inventory, config) if inventory.scope == "global" else ()
    if builder.skill_policies:
        config["skills"] = builder.skill_policies
    builder.write(
        Path(PROJECT_CONFIG_NAME if inventory.scope == "project" else MANIFEST_NAME),
        tomlkit.dumps(config).encode(),
    )
    states = {s.path: s for s in inventory.preconditions}
    for path in (*builder.writes, *builder.records, inventory.root / ".gitignore"):
        for state in path_preconditions(path):
            states.setdefault(state.path, state)
    source_conflicts = tuple(
        Issue(
            "source-exists",
            (path,),
            "The proposed source path already exists; select a source directory or resolve its ownership explicitly.",
        )
        for path in builder.writes
        if path.exists() or path.is_symlink()
    )
    if source_conflicts:
        return MigrationPlan(
            inventory, preconditions=tuple(states.values()), issues=source_conflicts
        )
    private_namespaces = tuple(
        dict.fromkeys(
            (
                *(
                    inventory.source_root
                    / w.path.relative_to(inventory.source_root).parts[0]
                    / "local"
                    for w in builder.writes.values()
                    if w.private and w.path.relative_to(inventory.source_root).parts[0] != "skills"
                ),
                *(
                    inventory.source_root / "skills" / f"{name}.local"
                    for name in sorted(builder.private_skill_names)
                ),
            )
        )
    )
    private_paths = tuple(
        dict.fromkeys(
            (
                *(
                    path
                    for c in inventory.candidates
                    if c.private
                    or c.disposition == "runtime-private exclusion"
                    or _partial(c, inventory.scope)
                    for path in _original_paths(c)
                ),
                *private_namespaces,
                *(w.path for w in builder.writes.values() if w.private),
            )
        )
    )
    ignores = [
        "/" + relative.as_posix() + ("/" if p in private_namespaces else "")
        for p in (*builder.records, *private_paths)
        if (relative := _relative_original(p, inventory.root)) is not None
    ]
    originals = _original_entries(builder, private_paths)
    obsolete = tuple(
        {entry_path(item.path): item.path for item in originals if item.action == "retire"}.values()
    )
    categories = _readiness(builder)
    plan = MigrationPlan(
        inventory,
        source_writes=tuple(builder.writes.values()),
        expected_outputs=tuple(builder.expected.values()),
        obsolete=obsolete,
        originals=originals,
        ignores=tuple(dict.fromkeys(ignores)),
        private_paths=private_paths,
        preconditions=tuple(states.values()),
        categories=categories,
        destination_routes=(
            ()
            if inventory.scope == "project"
            else (
                *preset_routes,
                *(
                    (record.get("destination", record.get("output", "")), path)
                    for path, record in builder.records.items()
                ),
            )
        ),
        notes=(
            "Native documents may be reformatted; ordered keys, literal values and opaque bytes/modes are validated.",
            "Secret detection is a heuristic and cannot establish that authored content is secret-free; review the source before pushing it.",
            *builder.notes,
        ),
        unadopted=tuple(builder.unadopted),
        secrets=_secrets(builder.writes.values()),
    )
    return validate_plan(plan)


def _owner_only(mode: int) -> bool:
    return not mode & 0o077


def _source_mode(mode: int, content: bytes, path: Path, *, restricted: bool = False) -> int:
    """Owner-only when it holds a secret or came from a file only its owner could read."""
    if restricted or credential_material(content, path.suffix[1:]) is not None:
        return mode & 0o700
    return mode


def _secrets(writes: Iterable[SourceWrite]) -> tuple[tuple[Path, str], ...]:
    """Each committable source file that looks like it holds a secret, and why (ADR 0030)."""
    return tuple(
        (write.path, reason)
        for write in writes
        if not write.private
        and (reason := credential_material(write.content, write.path.suffix[1:])) is not None
    )


def _conflicting_skills(
    inventory: Inventory, candidates: tuple[Candidate, ...]
) -> dict[str, list[Candidate]]:
    """Catalog skills whose copies in two skills folders write one source file with
    different bytes or modes, which adoption cannot merge without judgement."""
    copies: dict[str, dict[Path, list[Candidate]]] = defaultdict(lambda: defaultdict(list))
    for mapping in inventory.mappings:
        if mapping.kind == "file":
            continue
        target = mapping.destination / "skills"
        entries = [
            c
            for c in candidates
            if c.destination is not None and c.destination.is_relative_to(target)
        ]
        if not entries or any(c.format != "copy" for c in entries):
            continue
        for candidate in entries:
            assert candidate.destination is not None
            name = candidate.destination.relative_to(target).parts[0]
            copies[name][target].append(candidate)
    conflicting: dict[str, list[Candidate]] = {}
    for name, by_folder in copies.items():
        trees = [
            {
                c.destination.relative_to(folder / name): (c.content, c.mode)
                for c in group
                if c.destination is not None
            }
            for folder, group in by_folder.items()
            if any(c.destination == folder / name / SKILL_DOCUMENT for c in group)
        ]
        if any(
            first[path] != second[path]
            for index, first in enumerate(trees)
            for second in trees[index + 1 :]
            for path in first.keys() & second.keys()
        ):
            conflicting[name] = [c for group in by_folder.values() for c in group]
    return conflicting


def _adopt_agents(builder: _PlanBuilder, candidates: tuple[Candidate, ...]) -> None:
    """One source agent per name. Copies whose description or instructions differ are
    left out for /loadout to merge; one adoption cannot read stays where it is."""
    copies: dict[str, list[tuple[str, Candidate, NativeAgent]]] = defaultdict(list)
    for candidate in candidates:
        if candidate.category != "agents" or candidate.destination is None:
            continue
        harness = candidate.harnesses[0]
        problem = None
        try:
            native = extract_agent(
                harness, candidate.content.decode("utf-8"), candidate.destination.stem
            )
        except (LoadoutError, UnicodeError, ValueError) as error:
            problem = str(error)
        else:
            if not native.description:
                problem = "it has no description, which every harness requires of an agent"
            elif candidate.destination.name != agent_file_name(native.name, harness):
                problem = f"its name {native.name!r} does not match its file name"
        if problem is not None:
            builder.retained.update(_original_paths(candidate))
            builder.notes.append(f"{candidate.destination}: not adopted, {problem}")
            continue
        copies[native.name].append((harness, candidate, native))
    for name, group in sorted(copies.items()):
        texts = {(n.description, n.body.strip()) for _, _, n in group}
        if len(texts) > 1:
            builder.leave_out("agent", name, [c for _, c, _ in group])
            continue
        source = source_agent([(harness, native) for harness, _, native in group])
        builder.write(Path("agents") / f"{name}.md", source.encode())
        if builder.inventory.scope != "project":
            # Global scope reaches the agents folders through each block's preset route.
            continue
        for harness, candidate, _ in group:
            assert candidate.destination is not None
            builder.records[candidate.destination] = builder.record(
                candidate.destination, (harness,), "copy"
            )


def _item_path(kind: UnadoptedKind, name: str, candidate: Candidate) -> Path:
    """The skill folder a candidate file belongs to, or the instruction file itself."""
    path = candidate.destination or candidate.path
    if kind != "skill":
        return path
    while path.name != name:
        path = path.parent
    return path


def _imports_agents_md(candidate: Candidate) -> bool:
    """A CLAUDE.md holding only an import of AGENTS.md, with or without loadout's banner."""
    if candidate.path.name != "CLAUDE.md":
        return False
    try:
        text = candidate.content.decode("utf-8").strip()
    except UnicodeDecodeError:
        return False
    return text.removeprefix(HEADER).strip() in {"@AGENTS.md", "@./AGENTS.md"}


def _original_paths(candidate: Candidate) -> tuple[Path, ...]:
    if candidate.canonical is None:
        return (candidate.path,)
    return tuple(dict.fromkeys((candidate.path, candidate.canonical)))


def _relative_original(path: Path, root: Path) -> Path | None:
    for base in (root, root.resolve()):
        if path.is_relative_to(base):
            return path.relative_to(base)
    return None


def _original_entries(
    builder: _PlanBuilder, private_paths: tuple[Path, ...]
) -> tuple[OriginalEntry, ...]:
    inventory = builder.inventory
    layout = inventory_layout(inventory)
    # Sets walked by each path's ancestors, so the cost is the path's depth rather than
    # the number of entries (review 3, batch 3.1: 200 skills took 25 s here).
    private = frozenset(entry_path(path) for path in private_paths)
    destinations = frozenset(layout.normalize(path) for path in builder.records)
    holding = frozenset(p for d in destinations for p in (d, *d.parents))
    trees = frozenset(
        layout.normalize(destination)
        for destination, record in builder.records.items()
        if record["format"] == "tree"
    )
    retained = frozenset(
        entry_path(path)
        for candidate in inventory.candidates
        if candidate.disposition != "migrated"
        for path in _original_paths(candidate)
    )
    entries: dict[Path, OriginalEntry] = {}
    action: Literal["retain", "generated", "retire"]
    for candidate in inventory.candidates:
        for path in _original_paths(candidate):
            entry = entry_path(path)
            planned = layout.normalize(path)
            if path in builder.unadopted_paths:
                action, reason = (
                    "retain",
                    "Its copies disagree across harnesses, so it stays in place for /loadout to merge.",
                )
            elif path in builder.absorbed:
                action, reason = "retire", builder.absorbed[path]
            elif _within_any(entry, private):
                action, reason = (
                    "retain",
                    "Private or runtime original remains excluded and in place.",
                )
            elif path in builder.retained or _within_any(entry, retained):
                action, reason = "retain", candidate.reason
            elif planned in holding or _within_any(planned, trees):
                action, reason = "generated", "This entry remains a required generated destination."
            elif inventory.scope == "project" and path != candidate.path:
                # Read through a skills folder link init replaces: the folder it led to stays.
                action, reason = "retain", "The link to it becomes a real folder; this stays."
            elif not entry.is_relative_to(inventory.root.resolve()):
                action, reason = (
                    "retain",
                    "External original remains outside the retirement boundary.",
                )
            else:
                action, reason = (
                    "retire",
                    "The selected replacement reconstructs at its deployed destination.",
                )
            entries.setdefault(path, OriginalEntry(path, candidate.destination, action, reason))
    return tuple(entries.values())


def _within_any(path: Path, roots: frozenset[Path]) -> bool:
    """Whether `path` is one of `roots` or lies beneath one."""
    return path in roots or not roots.isdisjoint(path.parents)


def _readiness(builder: _PlanBuilder) -> tuple[CategoryReadiness, ...]:
    result = []
    agents_source = builder.inventory.source_root / "agents"
    adopted_agents = any(path.is_relative_to(agents_source) for path in builder.writes)
    for category in sorted(READINESS_CATEGORIES):
        for agent in builder.inventory.harnesses:
            paths = tuple(
                path
                for path, record in builder.records.items()
                if agent in record["harnesses"] and record.get("category") == category
            )
            if paths:
                result.append(CategoryReadiness(category, (agent,), paths, "routed"))
            elif category == "agents" and adopted_agents:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (agents_source,),
                        "routed",
                        "Source agents render to every configured harness.",
                    )
                )
            elif category == "skills" and (agent, category) in builder.available_categories:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (builder.inventory.source_root / "skills",),
                        "routed",
                        "Canonical skills default to every configured agent.",
                    )
                )
            elif (
                category == "hooks"
                and agent == "opencode"
                and (
                    plugin_paths := tuple(
                        path
                        for path, record in builder.records.items()
                        if agent in record["harnesses"] and record.get("category") == "plugins"
                    )
                )
            ):
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        plugin_paths,
                        "routed",
                        "OpenCode hooks are plugin code.",
                    )
                )
            elif (
                builder.inventory.scope == "project"
                and (agent, category) in builder.available_categories
            ) or category in {"support", "module-config", "templates"}:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (),
                        "not-adopted",
                        NOT_ADOPTED,
                    )
                )
            else:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (),
                        "unsupported",
                        "No verified independent destination for this scope/category; existing files stay in place.",
                    )
                )
    return tuple(result)
