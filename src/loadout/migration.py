from __future__ import annotations

import json
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import tomlkit

from .artifacts import CATEGORIES, _toml_item
from .discovery import PROJECT_ROOTS, credential_material, digest, path_preconditions
from .errors import LoadoutError
from .extract import extract
from .manifest import load_manifest
from .migration_models import (
    RENDERED_MODE,
    Candidate,
    CategoryReadiness,
    ExpectedOutput,
    Inventory,
    Issue,
    MigrationPlan,
    OriginalEntry,
    SourceSelection,
    SourceWrite,
)
from .migration_paths import DestinationLayout, entry_path
from .migration_validation import validate_plan
from .native_documents import key_fingerprints, parse_document
from .permissions.renderers import RENDERERS, JsonSpec, TextSpec
from .permissions.rules import Rules, parse_rules
from .project import load_project_config

RUNTIME_KEYS = {
    "codex": frozenset({"projects", "trust"}),
    "pi": frozenset({"lastChangelogVersion"}),
}
OWNERS: dict[str, dict[str, tuple[str, ...]]] = {
    "claude": {
        "permissions": ("permissions",),
        "hooks": ("hooks",),
        "plugins": ("enabledPlugins", "extraKnownMarketplaces"),
    },
    "codex": {"mcp": ("mcp_servers",), "plugins": ("plugins", "marketplaces")},
    "opencode": {"permissions": ("permission",), "mcp": ("mcp",), "plugins": ("plugin",)},
    "pi": {"plugins": ("packages",)},
}
TREE_CATEGORIES = {
    "skills": "skills",
    "commands": "instructions",
    "agents": "instructions",
    "rules": "instructions",
    "hooks": "hooks",
    "plugins": "plugins",
    "plugin": "plugins",
    "scripts": "support",
    "providers": "support",
    "profiles": "support",
}


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


def _same_document(first: bytes, second: bytes, format_name: str) -> bool:
    a = parse_document(first.decode(), format_name)
    b = parse_document(second.decode(), format_name)
    return tuple(a) == tuple(b) and key_fingerprints(
        first.decode(), format_name, frozenset(a)
    ) == key_fingerprints(second.decode(), format_name, frozenset(b))


def _portable(content: bytes, renderer: str, format_name: str) -> bytes | None:
    try:
        original = (
            parse_document(content.decode(), format_name)
            if format_name == "json"
            else content.decode()
        )
        extracted = extract(renderer, original)
        serialized = _rules_source(extracted.rules)
        with tempfile.TemporaryDirectory(prefix="loadout-rule-probe-") as scratch:
            source = Path(scratch) / "permissions.toml"
            source.write_bytes(serialized)
            rules = parse_rules(source)
        spec = RENDERERS[renderer]
        if isinstance(spec, JsonSpec):
            rendered = serialize_document(spec.fn(rules, {}), "json")
            return serialized if _same_document(content, rendered, "json") else None
        assert isinstance(spec, TextSpec)
        return serialized if content == spec.fn(rules).encode() else None
    except (LoadoutError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        return None


def _partial(candidate: Candidate) -> bool:
    if candidate.format not in {"json", "toml"}:
        return False
    return (
        candidate.path.name == ".claude.json"
        or (candidate.agents[0] == "codex" and candidate.path.name == "config.toml")
        or (candidate.agents[0] == "pi" and candidate.path.name == "settings.json")
    )


def _authored(candidate: Candidate) -> Candidate:
    if candidate.format not in {"json", "toml"} or not _partial(candidate):
        return candidate
    document = parse_document(candidate.content.decode(), candidate.format)
    authored = (
        {key: value for key, value in document.items() if key == "mcpServers"}
        if candidate.path.name == ".claude.json"
        else {
            key: value
            for key, value in document.items()
            if key not in RUNTIME_KEYS.get(candidate.agents[0], frozenset())
        }
    )
    content = serialize_document(authored, candidate.format)
    return replace(
        candidate,
        content=content,
        private=credential_material(content, candidate.format) or candidate.personal,
    )


def _choose(
    inventory: Inventory, selections: tuple[SourceSelection, ...]
) -> tuple[tuple[Candidate, ...], tuple[Issue, ...]]:
    grouped: dict[Path, list[Candidate]] = defaultdict(list)
    issues: list[Issue] = []
    for candidate in inventory.candidates:
        if candidate.disposition != "migrated" or candidate.destination is None:
            continue
        try:
            grouped[candidate.destination].append(_authored(candidate))
        except (LoadoutError, UnicodeError, ValueError, TypeError):
            issues.append(
                Issue(
                    "invalid-native-document",
                    (candidate.path,),
                    "Native document could not be parsed safely; repair it or provide an explicit opaque-file mapping.",
                )
            )
    chosen = []
    for destination, candidates in grouped.items():
        selection = next((s for s in selections if s.destination == destination), None)
        if selection is None:
            ancestor = next(
                (s for s in selections if destination.is_relative_to(s.destination)), None
            )
            selection = (
                SourceSelection(
                    destination, ancestor.source / destination.relative_to(ancestor.destination)
                )
                if ancestor
                else None
            )
        if selection is not None:
            selected = next((c for c in candidates if c.path == selection.source), None)
            if selected is None:
                issues.append(
                    Issue(
                        "invalid-source-selection",
                        (selection.source, destination),
                        "The selected source is not a candidate for this destination.",
                    )
                )
                continue
        elif len({(c.content, c.mode) for c in candidates}) > 1:
            issues.append(
                Issue(
                    "source-conflict",
                    tuple(c.path for c in candidates),
                    "Different originals map to one destination; select the authoritative source explicitly.",
                )
            )
            continue
        else:
            selected = candidates[0]
        chosen.append(
            replace(
                selected,
                agents=tuple(dict.fromkeys(a for c in candidates for a in c.agents)),
                private=any(c.private for c in candidates),
            )
        )
    return tuple(chosen), tuple(issues)


def _destination_template(inventory: Inventory, destination: Path) -> str:
    for mapping in sorted(inventory.mappings, key=lambda m: len(m.destination.parts), reverse=True):
        if mapping.destination_template is None:
            continue
        if mapping.kind == "file" and mapping.destination == destination:
            return mapping.destination_template
        if mapping.kind == "file" or not destination.is_relative_to(mapping.destination):
            continue
        return f"{mapping.destination_template}/{destination.relative_to(mapping.destination).as_posix()}"
    return str(destination)


class _PlanBuilder:
    def __init__(self, inventory: Inventory) -> None:
        self.inventory = inventory
        self.records: dict[Path, dict[str, Any]] = {}
        self.writes: dict[Path, SourceWrite] = {}
        self.expected: dict[Path, ExpectedOutput] = {}
        self.issues: list[Issue] = []
        self.notes: list[str] = []
        self.selected: tuple[Candidate, ...] = ()

    def source(
        self,
        category: str,
        agents: tuple[str, ...],
        destination: Path,
        *,
        private: bool = False,
        suffix: str = "",
    ) -> Path:
        if self.inventory.scope == "project":
            relative = destination.relative_to(self.inventory.root)
        else:
            mapping = next(
                (
                    m
                    for m in sorted(
                        self.inventory.mappings,
                        key=lambda m: len(m.destination.parts),
                        reverse=True,
                    )
                    if destination.is_relative_to(m.destination)
                ),
                None,
            )
            relative = (
                destination.relative_to(mapping.destination)
                if mapping and mapping.kind != "file"
                else Path(destination.name)
            )
            if mapping and mapping.destination_template in {
                "${OPENCODE_CONFIG}",
                "${OPENCODE_CONFIG_DIR}",
            }:
                relative = (
                    Path(
                        "supplemental-file" if mapping.kind == "file" else "supplemental-directory"
                    )
                    / relative
                )
            elif mapping and mapping.destination_template is None:
                relative = (
                    Path("mapped-" + digest(str(mapping.destination).encode())[:12]) / relative
                )
        name = Path(category) / ("local" if private else "native") / "+".join(agents) / relative
        return name.with_name(name.name + suffix) if suffix else name

    def write(
        self, relative: Path, content: bytes, *, mode: int = 0o644, private: bool = False
    ) -> None:
        path = self.inventory.source_root / relative
        item = SourceWrite(path, content, mode, private)
        previous = self.writes.get(path)
        if previous is not None and previous != item:
            raise LoadoutError(
                "Migration sources collide; provide distinct explicit destination mappings."
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
        return {"agents": list(agents), key: value, "format": format_name}

    def expect(self, candidate: Candidate, *, owned: tuple[str, ...] | None = None) -> None:
        assert candidate.destination is not None
        content = candidate.content
        if candidate.format in {"json", "toml"}:
            keys = frozenset(parse_document(content.decode(), candidate.format))
            fingerprints = key_fingerprints(content.decode(), candidate.format, keys)
            if owned is not None and not keys:
                return
            self.expected[candidate.destination] = ExpectedOutput(
                candidate.destination,
                candidate.format,
                digest(content),
                mode=next(
                    (
                        c.mode
                        for c in self.inventory.candidates
                        if c.path == candidate.destination and c.disposition == "migrated"
                    ),
                    0o600,
                )
                if owned is not None
                else 0o600,
                owned=owned,
                fingerprints=fingerprints,
            )
        else:
            self.expected[candidate.destination] = ExpectedOutput(
                candidate.destination, "copy", digest(content), candidate.mode
            )

    def document(self, candidate: Candidate, *, existing: bool = True) -> None:
        assert candidate.destination is not None
        document = parse_document(candidate.content.decode(), candidate.format)
        agent = candidate.agents[0]
        special = candidate.category in {"mcp", "hooks", "permissions", "mcp-permissions"}
        composite = candidate.path.name in {
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
        parts: dict[str, Any] = {}
        values = {
            category: {key: value for key, value in document.items() if key in keys}
            for category, keys in ownership.items()
        }
        residual = {key: value for key, value in document.items() if key not in claimed}
        if not special or residual:
            values = {"settings": residual, **values}
        for category, value in values.items():
            content = serialize_document(value, candidate.format)
            private = candidate.private or credential_material(content, candidate.format)
            source = self.source(category, candidate.agents, candidate.destination, private=private)
            part: dict[str, Any] = {"source": source.as_posix()}
            if private:
                part["optional"] = True
            if category in ownership:
                part["keys"] = list(ownership[category])
            renderer = (
                _permission_renderer(candidate, self.inventory.scope == "project")
                if category in {"permissions", "mcp-permissions"}
                else None
            )
            portable = (
                _portable(content, renderer, "json")
                if renderer and candidate.format == "json" and value
                else None
            )
            if portable is not None:
                content = portable
                source = source.with_suffix(".rules.toml")
                part.update(source=source.as_posix(), renderer=renderer)
                self.notes.append(
                    f"Portable permission source validated for {candidate.destination}."
                )
            self.write(source, content, mode=0o600 if private else 0o644, private=private)
            parts[category] = part
        record = self.record(candidate.destination, candidate.agents, candidate.format)
        record.update(parts=parts, order=list(document))
        if existing:
            record["emit_empty"] = True
        if _partial(candidate):
            record["partial"] = True
            if not document:
                record["emit_empty"] = False
        self.records[candidate.destination] = record
        if existing:
            owned = tuple(
                sorted(
                    {
                        key
                        for category, value in values.items()
                        for key in ownership.get(category, tuple(value))
                    }
                )
            )
            self.expect(candidate, owned=owned if _partial(candidate) else None)

    def opaque(self, candidate: Candidate, *, existing: bool = True) -> None:
        assert candidate.destination is not None
        source = self.source(
            candidate.category, candidate.agents, candidate.destination, private=candidate.private
        )
        format_name = "copy"
        content = candidate.content
        renderer = (
            _permission_renderer(candidate, self.inventory.scope == "project")
            if candidate.category == "permissions"
            else None
        )
        portable = _portable(content, renderer, "text") if renderer and content else None
        record = self.record(candidate.destination, candidate.agents, format_name)
        if portable is not None and candidate.mode == RENDERED_MODE:
            content = portable
            source = source.with_suffix(".rules.toml")
            record.update(format="text", renderer=renderer)
        self.write(source, content, mode=candidate.mode, private=candidate.private)
        record.update(category=candidate.category, source=source.as_posix())
        if candidate.private:
            record["optional"] = True
        if existing:
            record["emit_empty"] = True
            self.expect(candidate)
        self.records[candidate.destination] = record

    def tree(
        self,
        destination: Path,
        agents: tuple[str, ...],
        category: str,
        candidates: tuple[Candidate, ...],
    ) -> None:
        private = any(c.private for c in candidates)
        source = self.source(category, agents, destination, private=private)
        self.write(source / ".gitkeep", b"", private=private)
        for candidate in candidates:
            assert candidate.destination is not None
            self.write(
                source / candidate.destination.relative_to(destination),
                candidate.content,
                mode=candidate.mode,
                private=private,
            )
            self.expect(candidate)
        record = self.record(destination, agents, "tree")
        record.update(category=category, source=source.as_posix())
        if private:
            record["optional"] = True
        self.records[destination] = record

    def originals(self, candidates: tuple[Candidate, ...]) -> None:
        self.selected = candidates
        remaining = {c.destination: c for c in candidates}
        for mapping in self.inventory.mappings:
            if mapping.kind == "file":
                continue
            for directory, category in TREE_CATEGORIES.items():
                if directory == "rules" and "codex" in mapping.agents:
                    continue
                target = mapping.destination / directory
                entries = tuple(
                    c for p, c in remaining.items() if p is not None and p.is_relative_to(target)
                )
                if not entries or any(c.format != "copy" for c in entries):
                    continue
                agents = tuple(dict.fromkeys(a for c in entries for a in c.agents))
                self.tree(target, agents, category, entries)
                for candidate in entries:
                    remaining.pop(candidate.destination)
        for candidate in remaining.values():
            if candidate.format in {"json", "toml"}:
                self.document(candidate)
            else:
                self.opaque(candidate)

    def dormant(
        self, destination: Path, agents: tuple[str, ...], category: str, format_name: str = "copy"
    ) -> None:
        if any(
            destination.is_relative_to(p) or p.is_relative_to(destination) for p in self.records
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
        if format_name == "tree":
            self.tree(destination, agents, category, ())
        elif format_name in {"json", "toml"}:
            self.document(candidate, existing=False)
        else:
            self.opaque(candidate, existing=False)

    def starters(self) -> None:
        project = self.inventory.scope == "project"
        for agent in self.inventory.agents:
            root = (
                self.inventory.root / PROJECT_ROOTS[agent]
                if project
                else next(
                    m.destination
                    for m in self.inventory.mappings
                    if m.kind == "harness" and m.agents == (agent,)
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
                tuple(a for a in self.inventory.agents if a != "claude")
                if project and agent != "claude"
                else (agent,)
            )
            self.dormant(instruction, consumers, "instructions")
            if not (project and agent == "codex"):
                self.dormant(root / "skills", (agent,), "skills", "tree")
            if agent == "claude":
                self.dormant(root / "hooks", (agent,), "hooks", "tree")
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
                    tuple(a for a in ("claude", "pi") if a in self.inventory.agents)
                    if project
                    else (agent,),
                    "mcp",
                    "json",
                )
            elif agent == "codex":
                self.dormant(root / "rules/permissions.rules", (agent,), "permissions")
                if not project:
                    self.dormant(root / "hooks.json", (agent,), "hooks", "json")
                    self.dormant(root / "plugins", (agent,), "plugins", "tree")
            elif agent == "opencode":
                self.dormant(root / "plugins", (agent,), "plugins", "tree")
            elif agent == "pi":
                self.dormant(
                    root / "extensions/pi-permission-system/config.json",
                    (agent,),
                    "permissions",
                    "json",
                )
                self.dormant(root / "extensions/loadout.ts", (agent,), "hooks")
                self.dormant(
                    self.inventory.root / ".mcp.json" if project else root / "mcp.json",
                    (agent,),
                    "mcp",
                    "json",
                )
        for category in sorted(CATEGORIES):
            self.write(Path(category) / ".gitkeep", b"")
        for category in ("module-config", "support", "templates"):
            self.write(
                Path(category) / "README.md",
                (
                    b"Add an explicit artifact binding in artifacts.toml for each new destination.\n"
                    b"Existing routes preserve their authored paths. A module chooses its own filename;\n"
                    b"templates require an explicit selection before their contents become active.\n"
                ),
            )


def _permission_renderer(candidate: Candidate, project: bool) -> str | None:
    agent = candidate.agents[0]
    if candidate.category == "mcp-permissions" and agent == "claude":
        return "claude-mcp-permissions"
    if agent == "claude":
        return "claude-project" if project else "claude"
    if agent == "codex" and candidate.path.suffix == ".rules":
        return "codex-project" if project else "codex"
    if agent == "pi":
        return "pi-project" if project else "pi"
    return "opencode" if agent == "opencode" else None


def _initialized(inventory: Inventory) -> MigrationPlan:
    assert inventory.initialized is not None
    config_path = inventory.initialized
    try:
        if inventory.scope == "project":
            config = load_project_config(config_path)
            if inventory.agents and set(inventory.agents) != set(config.harnesses):
                raise LoadoutError("configured harness selection differs")
            inventory = replace(inventory, agents=config.harnesses)
        else:
            manifest = load_manifest(config_path)
            agents = tuple(
                dict.fromkeys(
                    (
                        *(manifest.artifacts.agents() if manifest.artifacts else ()),
                        *(p.agent for p in manifest.permissions if p.agent),
                        *(
                            p.renderer.split("-", 1)[0]
                            for p in manifest.permissions
                            if p.renderer.split("-", 1)[0] in PROJECT_ROOTS
                        ),
                        *(t.name for t in manifest.targets if t.name in PROJECT_ROOTS),
                        *(s.agent for s in manifest.skills),
                        *(m.agent for m in manifest.module_config),
                    )
                )
            )
            if inventory.agents and set(inventory.agents) != set(agents):
                raise LoadoutError("configured harness selection differs")
            inventory = replace(inventory, agents=agents)
    except (LoadoutError, OSError, ValueError):
        return MigrationPlan(
            inventory,
            issues=(
                Issue(
                    "initialized-source",
                    (config_path,),
                    "An existing Loadout source needs repair or a matching harness selection; it will not be reinterpreted as raw configuration.",
                ),
            ),
        )
    return MigrationPlan(
        inventory,
        preconditions=inventory.preconditions,
        already_initialized=True,
        notes=(
            "Existing Loadout source recognized. No migration is planned; use normal check/sync for its managed scope.",
        ),
    )


def plan_migration(
    inventory: Inventory, *, selections: tuple[SourceSelection, ...] = ()
) -> MigrationPlan:
    if inventory.initialized is not None:
        return _initialized(inventory)
    candidates, conflicts = _choose(inventory, selections)
    issues = (*inventory.issues, *conflicts)
    if issues:
        return MigrationPlan(inventory, preconditions=inventory.preconditions, issues=issues)
    builder = _PlanBuilder(inventory)
    try:
        builder.originals(candidates)
        builder.starters()
    except (LoadoutError, UnicodeError, ValueError, TypeError):
        return MigrationPlan(
            inventory,
            issues=(
                Issue(
                    "source-composition",
                    (inventory.source_root,),
                    "The native sources could not be composed safely; provide explicit artifact mappings.",
                ),
            ),
        )
    config = (
        {"harnesses": list(inventory.agents), "presets": False, "artifacts": "artifacts.toml"}
        if inventory.scope == "project"
        else {"artifacts": "artifacts.toml"}
    )
    builder.write(
        Path("config.toml" if inventory.scope == "project" else "loadout.toml"),
        tomlkit.dumps(config).encode(),
    )
    builder.write(
        Path("artifacts.toml"), tomlkit.dumps({"artifact": list(builder.records.values())}).encode()
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
            inventory.source_root / w.path.relative_to(inventory.source_root).parts[0] / "local"
            for w in builder.writes.values()
            if w.private
        )
    )
    private_paths = tuple(
        dict.fromkeys(
            (
                *(
                    path
                    for c in inventory.candidates
                    if c.private or c.disposition == "runtime-private exclusion" or _partial(c)
                    for path in _original_paths(c)
                ),
                *private_namespaces,
                *(w.path for w in builder.writes.values() if w.private),
            )
        )
    )
    ignores = ["/loadout/.loadout-state/"]
    ignores += [
        "/" + relative.as_posix() + ("/" if p in private_namespaces else "")
        for p in (*builder.records, *private_paths)
        if (relative := _relative_original(p, inventory.root)) is not None
    ]
    originals = _original_entries(builder, private_paths)
    checkpoint = tuple(
        dict.fromkeys(
            item.path
            for item in originals
            if item.action != "retain" and _relative_original(item.path, inventory.root) is not None
        )
    )
    obsolete = tuple(
        {entry_path(item.path): item.path for item in originals if item.action == "retire"}.values()
    )
    categories = _readiness(builder)
    plan = MigrationPlan(
        inventory,
        source_writes=tuple(builder.writes.values()),
        expected_outputs=tuple(builder.expected.values()),
        required_absences=tuple(
            p
            for p, record in builder.records.items()
            if p not in builder.expected and record["format"] != "tree"
        ),
        obsolete=obsolete,
        originals=originals,
        ignores=tuple(dict.fromkeys(ignores)),
        private_paths=private_paths,
        checkpoint_paths=checkpoint,
        preconditions=tuple(states.values()),
        categories=categories,
        artifact_routes=tuple(
            (record.get("destination", record.get("output", "")), path)
            for path, record in builder.records.items()
        ),
        notes=(
            "Native documents may be reformatted; ordered keys, literal values and opaque bytes/modes are validated.",
            "Credential detection is conservative but cannot establish that authored content is secret-free; review checkpoint paths before approval.",
            *builder.notes,
        ),
    )
    return validate_plan(plan)


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
    home = dict(inventory.destination_environment).get("HOME")
    layout = DestinationLayout(inventory.root, inventory.mappings, Path(home) if home else None)
    private = tuple(entry_path(path) for path in private_paths)
    destinations = tuple(layout.normalize(path) for path in builder.records)
    retained = tuple(
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
            if any(entry.is_relative_to(p) for p in private):
                action, reason = (
                    "retain",
                    "Private or runtime original remains excluded and in place.",
                )
            elif any(entry.is_relative_to(p) for p in retained):
                action, reason = "retain", candidate.reason
            elif any(planned == p or p.is_relative_to(planned) for p in destinations) or any(
                record["format"] == "tree" and planned.is_relative_to(layout.normalize(destination))
                for destination, record in builder.records.items()
            ):
                action, reason = "generated", "This entry remains a required generated destination."
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


def _readiness(builder: _PlanBuilder) -> tuple[CategoryReadiness, ...]:
    result = []
    for category in sorted(CATEGORIES):
        for agent in builder.inventory.agents:
            paths = tuple(
                path
                for path, record in builder.records.items()
                if agent in record["agents"]
                and (record.get("category") == category or category in record.get("parts", {}))
            )
            if paths:
                result.append(CategoryReadiness(category, (agent,), paths, "routed"))
            elif category in {"support", "module-config", "templates"}:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (),
                        "explicit-binding",
                        "The module or selected template supplies its destination; add an artifact binding.",
                    )
                )
            elif category == "hooks" and agent == "opencode":
                plugin_paths = tuple(
                    path
                    for path, record in builder.records.items()
                    if agent in record["agents"] and record.get("category") == "plugins"
                )
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        plugin_paths,
                        "routed",
                        "OpenCode hooks are plugin code.",
                    )
                )
            else:
                result.append(
                    CategoryReadiness(
                        category,
                        (agent,),
                        (),
                        "unsupported",
                        "No verified independent destination for this scope/category; preserve explicit existing mappings.",
                    )
                )
    return tuple(result)
