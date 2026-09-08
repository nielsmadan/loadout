from __future__ import annotations

import hashlib
import os
import re
import shlex
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from .artifacts import CATEGORIES, ArtifactScope
from .errors import LoadoutError
from .git_privacy import ignored_paths
from .migration_models import Candidate, EntryState, Inventory, Issue, RootMapping
from .migration_paths import DestinationLayout
from .native_documents import parse_document
from .project import KNOWN_HARNESSES

GLOBAL_ROOTS = {
    "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
    "codex": ("CODEX_HOME", ".codex"),
    "opencode": ("XDG_CONFIG_HOME", ".config"),
    "pi": ("PI_CODING_AGENT_DIR", ".pi/agent"),
}
PROJECT_ROOTS = {"claude": ".claude", "codex": ".codex", "opencode": ".opencode", "pi": ".pi"}
SOURCE_LAYOUTS = {
    "claude": (".claude", "claude"),
    "codex": (".codex", "codex"),
    "opencode": (".config/opencode", "opencode"),
    "pi": (".pi/agent", ".pi", "pi"),
}
UNSUPPORTED_HARNESS_ROOTS = frozenset(
    {".gemini", ".cursor", ".windsurf", ".antigravity", ".kiro", ".continue"}
)
RUNTIME_DIRECTORIES = frozenset(
    {
        ".git",
        ".loadout-state",
        "node_modules",
        "__pycache__",
        "cache",
        "caches",
        "sessions",
        "projects",
        "history",
        "logs",
        "debug",
        "statsig",
        "telemetry",
        "shell-snapshots",
        "todos",
        "file-history",
        "session-env",
        "backups",
        "paste-cache",
        ".ssh",
        "secrets",
    }
)
RUNTIME_FILES = frozenset(
    {
        "auth.json",
        ".credentials.json",
        "credentials.json",
        "credentials",
        "history.jsonl",
        "history.json",
        "trust.json",
        "trusted-folders.json",
        "mcp-cache.json",
        "run-history.jsonl",
        "mcp-onboarding.json",
        "stats-cache.json",
        "session_index.jsonl",
        "known_marketplaces.json",
        "installed_plugins.json",
        "plugins_installation.json",
        ".env",
        ".env.local",
    }
)
INSTRUCTION_FILES = frozenset(
    {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", "opencode.md"}
)
WALK_EXCLUSIONS = RUNTIME_DIRECTORIES | frozenset(
    {".venv", "venv", "vendor", "dist", "build", "loadout"}
)
PROJECT_WALK_EXCLUSIONS = frozenset(
    {
        ".git",
        ".loadout-state",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "vendor",
        "dist",
        "build",
        "loadout",
    }
)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def entry_state(path: Path, *, read: bool = True) -> EntryState:
    path = path.absolute()
    try:
        value = path.lstat()
    except FileNotFoundError:
        return EntryState(path, "absent")
    mode = stat.S_IMODE(value.st_mode)
    if stat.S_ISLNK(value.st_mode):
        return EntryState(
            path, "symlink", mode, link=os.readlink(path), device=value.st_dev, inode=value.st_ino
        )
    if stat.S_ISDIR(value.st_mode):
        entries = (
            b"\0".join(os.fsencode(name) for name in sorted(os.listdir(path))) if read else None
        )
        return EntryState(
            path,
            "directory",
            mode,
            digest(entries) if entries is not None else None,
            device=value.st_dev,
            inode=value.st_ino,
        )
    if stat.S_ISREG(value.st_mode):
        return EntryState(path, "file", mode, digest(path.read_bytes()) if read else None)
    return EntryState(path, "special", mode, device=value.st_dev, inode=value.st_ino)


def path_preconditions(path: Path, *, read: bool = True) -> tuple[EntryState, ...]:
    return (
        entry_state(path, read=read),
        *(entry_state(p, read=False) for p in path.absolute().parents),
    )


def live_roots(home: Path, environ: Mapping[str, str]) -> tuple[RootMapping, ...]:
    result = []
    for agent, (variable, fallback) in GLOBAL_ROOTS.items():
        value = environ.get(variable)
        root = _home_path(value, home) if value else home / fallback
        if agent == "opencode":
            root /= "opencode"
        if not root.is_absolute() or ".." in root.parts:
            raise LoadoutError(f"{variable} must identify an absolute harness root")
        template = f"${{{variable}:-~/{fallback}}}" + ("/opencode" if agent == "opencode" else "")
        result.append(RootMapping(root, root, (agent,), destination_template=template))
    claude_parent = (
        _home_path(environ["CLAUDE_CONFIG_DIR"], home) if environ.get("CLAUDE_CONFIG_DIR") else home
    )
    result.append(
        RootMapping(
            claude_parent / ".claude.json",
            claude_parent / ".claude.json",
            ("claude",),
            "file",
            "mcp",
            "${CLAUDE_CONFIG_DIR:-~}/.claude.json",
        )
    )
    result.append(
        RootMapping(
            home / ".agents",
            home / ".agents",
            ("codex", "opencode", "pi"),
            "shared",
            destination_template="~/.agents",
        )
    )
    shared_mcp = (
        _home_path(environ["XDG_CONFIG_HOME"], home) / "mcp/mcp.json"
        if environ.get("XDG_CONFIG_HOME")
        else home / ".config/mcp/mcp.json"
    )
    result.append(
        RootMapping(
            shared_mcp,
            shared_mcp,
            ("pi",),
            "file",
            "mcp",
            "${XDG_CONFIG_HOME:-~/.config}/mcp/mcp.json",
        )
    )
    for variable, kind, category in (
        ("OPENCODE_CONFIG", "file", "settings"),
        ("OPENCODE_CONFIG_DIR", "harness", None),
    ):
        if environ.get(variable):
            path = _home_path(environ[variable], home)
            if not path.is_absolute() or ".." in path.parts:
                raise LoadoutError(f"{variable} must identify an absolute harness path")
            result.append(
                RootMapping(
                    path,
                    path,
                    ("opencode",),
                    "file" if kind == "file" else "harness",
                    category,
                    "${" + variable + "}",
                )
            )
    return tuple(result)


def project_root(path: Path) -> Path:
    path = path.absolute()
    for parent in (path, *path.parents):
        if (parent / "loadout/config.toml").is_file() or (parent / ".git").exists():
            return parent
    return path


def _home_path(value: str, home: Path) -> Path:
    return home / value[2:] if value.startswith("~/") else home if value == "~" else Path(value)


def _excluded(path: Path) -> bool:
    if not path.parts:
        return False
    if path.parts[0] in RUNTIME_DIRECTORIES or path.name in {".env", ".env.local"}:
        return True
    if path.parts[:2] in {("plugins", "cache"), ("plugins", "marketplaces")}:
        return True
    return path.name in RUNTIME_FILES or (
        len(path.parts) == 1 and path.suffix in {".db", ".sqlite", ".sqlite3"}
    )


def _private(path: Path) -> bool:
    return ".local." in path.name or path.name.endswith(".local")


def credential_material(content: bytes, format_name: str = "copy") -> bool:
    text = content.decode("utf-8", errors="replace")
    if re.search(
        r"(?i)(?:bearer\s+(?!\$|\{)[\w.+/=-]+|(?:api[_-]?key|password|secret|token)\s*[:=]\s*[\"']?(?!\$|\{)[\w.+/=-]+)",
        text,
    ):
        return True
    if format_name not in {"json", "toml"}:
        return False
    try:
        document = parse_document(text, format_name)
    except LoadoutError:
        return False
    return _credential_value(document)


def _credential_value(value: object, key: str = "") -> bool:
    if isinstance(value, dict):
        return any(
            _credential_value(v, str(k))
            or (
                key.lower() in {"env", "headers", "http_headers"}
                and isinstance(v, str)
                and bool(v)
                and not _environment_reference(v)
            )
            for k, v in value.items()
        )
    if isinstance(value, list):
        return any(_credential_value(v, key) for v in value)
    sensitive = re.search(
        r"(?i)(?:authorization|credential|password|secret|token|api[_-]?key)", key
    )
    return bool(
        sensitive
        and isinstance(value, str)
        and value
        and not _environment_reference(value)
        and not key.endswith("_env_var")
    )


def _environment_reference(value: str) -> bool:
    return bool(
        re.fullmatch(r"(?:Bearer )?(?:\$\{[^}]+\}|\{env:[^}]+\}|\$[A-Z_][A-Z_0-9]*)", value)
    )


def _category(relative: Path, agent: str) -> str | None:
    first = relative.parts[0]
    if relative.name in INSTRUCTION_FILES or first in {"commands", "agents"}:
        return "instructions"
    if first == "rules":
        return "permissions" if agent == "codex" else "instructions"
    if first == "extensions":
        return (
            "permissions"
            if relative.as_posix() == "extensions/pi-permission-system/config.json"
            else "module-config"
        )
    directories = {
        "skills": "skills",
        "hooks": "hooks",
        "plugins": "plugins",
        "templates": "templates",
        "plugin": "plugins",
        "scripts": "support",
        "providers": "support",
        "profiles": "support",
    }
    names = {
        **dict.fromkeys((".mcp.json", "mcp.json", ".claude.json"), "mcp"),
        **dict.fromkeys(
            ("package.json", "package-lock.json", "bun.lock", "bun.lockb", "statusline.sh"),
            "support",
        ),
        **dict.fromkeys(
            (
                "settings.json",
                "settings.local.json",
                "config.toml",
                "opencode.json",
                "opencode.jsonc",
                "tui.json",
                "keybindings.json",
                "rtk-filters.toml",
                "models.json",
            ),
            "settings",
        ),
        "mcp-permissions.json": "mcp-permissions",
        "hooks.json": "hooks",
    }
    fallback = "module-config" if agent == "pi" and relative.suffix == ".json" else None
    return directories.get(first, names.get(relative.name, fallback))


def _agents_for(relative: Path, mapping: RootMapping) -> tuple[str, ...]:
    if mapping.kind != "shared":
        return mapping.agents
    supported = {
        "skills": {"codex", "opencode", "pi"},
        "plugins": {"codex"},
        "mcp.json": {"pi"},
        "mcp": {"pi"},
    }
    return tuple(a for a in mapping.agents if a in supported.get(relative.parts[0], set()))


class _InventoryBuilder:
    def __init__(
        self, layout: DestinationLayout, allowed: tuple[Path, ...], runtime_roots: tuple[Path, ...]
    ) -> None:
        self.root = layout.root
        self.layout = layout
        self.allowed = allowed
        self.runtime_roots = runtime_roots
        self.confirmed_roots: tuple[Path, ...] = ()
        self.candidates: dict[tuple[Path, Path | None], Candidate] = {}
        self.states: dict[Path, EntryState] = {}
        self.issues: list[Issue] = []

    def remember(self, path: Path, *, read: bool = True) -> None:
        for state in path_preconditions(path, read=read):
            previous = self.states.get(state.path)
            if previous is None or previous.digest is None:
                self.states[state.path] = state

    def walk(self, mapping: RootMapping, *, confirmed: bool = True) -> None:
        self._walk(mapping.source, mapping, (), confirmed=confirmed)

    def _walk(
        self, path: Path, mapping: RootMapping, ancestors: tuple[Path, ...], *, confirmed: bool
    ) -> None:
        if not confirmed and path in self.confirmed_roots:
            return
        if not path.exists() and not path.is_symlink():
            self.remember(path, read=False)
            return
        relative = path.relative_to(mapping.source) if mapping.kind != "file" else Path(path.name)
        destination = (
            mapping.destination / relative if mapping.kind != "file" else mapping.destination
        )
        try:
            canonical = path.resolve(strict=True)
        except (OSError, RuntimeError):
            self._unresolved(
                path,
                destination,
                mapping.agents,
                "symlink-target",
                "Resolve the missing or cyclic symlink target.",
            )
            return
        canonical_excluded = any(
            _excluded(canonical.relative_to(root))
            for root in self.runtime_roots
            if canonical.is_relative_to(root)
        )
        if _excluded(relative) or canonical_excluded:
            self.remember(path, read=False)
            self.candidates[path, destination] = Candidate(
                path,
                canonical,
                destination,
                mapping.agents,
                "runtime",
                "runtime-private exclusion",
                "Runtime, authentication or cache path; left in place.",
                private=True,
            )
            return
        self.remember(path, read=False)
        if not any(canonical.is_relative_to(root) for root in self.allowed):
            self._unresolved(
                path,
                destination,
                mapping.agents,
                "external-symlink",
                "Supply an explicit source/shared-root mapping for this symlink target.",
            )
            return
        self.remember(canonical, read=False)
        if canonical in ancestors:
            self._unresolved(
                path,
                destination,
                mapping.agents,
                "symlink-cycle",
                "Resolve the cyclic directory mapping.",
            )
            return
        if path.is_dir():
            self.remember(path)
            self.remember(canonical)
            for child in sorted(path.iterdir()):
                self._walk(child, mapping, (*ancestors, canonical), confirmed=confirmed)
        else:
            self._file(path, canonical, destination, mapping, confirmed=confirmed)

    def _file(
        self,
        path: Path,
        canonical: Path,
        destination: Path,
        mapping: RootMapping,
        *,
        confirmed: bool,
    ) -> None:
        relative = path.relative_to(mapping.source) if mapping.kind != "file" else Path(path.name)
        if not path.is_file():
            self._unresolved(
                path,
                destination,
                mapping.agents,
                "special-entry",
                "Map this non-regular filesystem entry explicitly.",
            )
            return
        self.remember(path)
        self.remember(canonical)
        if path.name == ".gitkeep":
            self.candidates[path, destination] = Candidate(
                path,
                canonical,
                destination,
                mapping.agents,
                "support",
                "retained dependency",
                "Empty-directory tracking marker remains at its original path.",
            )
            return
        agents = _agents_for(relative, mapping)
        category = mapping.category or _category(relative, agents[0] if agents else "")
        if not confirmed or not agents or category is None or path.suffix in {".tmpl", ".jsonc"}:
            self._unresolved(
                path,
                destination if confirmed else None,
                agents,
                "source-mapping",
                "Confirm this authored file's category, consumers and deployed destination.",
            )
            return
        content = path.read_bytes()
        native_location = len(relative.parts) == 1 or relative.as_posix() in {
            "extensions/pi-permission-system/config.json",
            "mcp/mcp.json",
        }
        document = (
            native_location
            and category
            in {
                "settings",
                "mcp",
                "mcp-permissions",
                "permissions",
                "hooks",
            }
            and destination.suffix in {".json", ".toml"}
        )
        format_name = destination.suffix[1:] if document else "copy"
        privacy_format = destination.suffix[1:] or path.suffix[1:]
        self.issues = [
            issue
            for issue in self.issues
            if not (issue.code == "source-mapping" and path in issue.paths)
        ]
        self.candidates[path, destination] = Candidate(
            path,
            canonical,
            destination,
            agents,
            category,
            "migrated",
            "Mapped native artifact.",
            content,
            stat.S_IMODE(path.stat().st_mode),
            _private(path) or credential_material(content, privacy_format),
            format_name,
            _private(path),
        )

    def _unresolved(
        self, path: Path, destination: Path | None, agents: tuple[str, ...], code: str, message: str
    ) -> None:
        self.remember(path, read=False)
        self.candidates[path, destination] = Candidate(
            path, None, destination, agents, "support", "unresolved", message
        )
        self.issues.append(Issue(code, (path,), message))


def _project_mappings(root: Path, agents: tuple[str, ...]) -> tuple[RootMapping, ...]:
    mappings = [
        RootMapping(root / relative, root / relative, (agent,))
        for agent, relative in PROJECT_ROOTS.items()
        if agent in agents
    ]
    for name, consumers, category in (
        ("opencode.json", ("opencode",), "settings"),
        ("opencode.jsonc", ("opencode",), "settings"),
        (".mcp.json", ("claude", "pi"), "mcp"),
    ):
        active = tuple(a for a in consumers if a in agents)
        if active:
            mappings.append(RootMapping(root / name, root / name, active, "file", category))
    mappings.append(RootMapping(root / ".agents", root / ".agents", agents, "shared"))
    return tuple(mappings)


def _instruction_mappings(
    builder: _InventoryBuilder, agents: tuple[str, ...]
) -> tuple[RootMapping, ...]:
    root = builder.root
    paths: set[Path] = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        builder.remember(Path(directory))
        builder.remember(Path(directory).resolve())
        directories[:] = sorted(
            d
            for d in directories
            if d not in PROJECT_WALK_EXCLUSIONS
            and d not in PROJECT_ROOTS.values()
            and d != ".agents"
        )
        paths.update(Path(directory) / name for name in set(files) & INSTRUCTION_FILES)
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached"], capture_output=True, check=False
    )
    if result.returncode == 0:
        paths.update(
            root / os.fsdecode(name)
            for name in result.stdout.split(b"\0")
            if name
            and Path(os.fsdecode(name)).name in INSTRUCTION_FILES
            and "loadout" not in Path(os.fsdecode(name)).parts
        )
    mappings = []
    for path in sorted(paths):
        name = path.name
        consumers = (
            ("claude", "opencode")
            if name.startswith("CLAUDE")
            else ("opencode",)
            if name == "opencode.md"
            else ("codex", "opencode", "pi")
        )
        active = tuple(a for a in consumers if a in agents)
        mappings.append(RootMapping(path, path, active, "file", "instructions"))
    return tuple(mappings)


def _validate_mappings(mappings: tuple[RootMapping, ...]) -> tuple[RootMapping, ...]:
    result = []
    for mapping in mappings:
        if not mapping.agents or set(mapping.agents) - KNOWN_HARNESSES:
            raise LoadoutError("root mappings require known consumer agents")
        if mapping.category is not None and mapping.category not in CATEGORIES:
            raise LoadoutError("root mapping requires a known artifact category")
        if mapping.kind not in {"harness", "shared", "file"}:
            raise LoadoutError("root mapping kind must be harness, shared or file")
        if (
            ".." in mapping.source.parts
            or not mapping.destination.is_absolute()
            or ".." in mapping.destination.parts
        ):
            raise LoadoutError("root mapping paths must stay inside their declared absolute roots")
        result.append(replace(mapping, source=mapping.source.absolute()))
    return tuple(result)


def discover(
    root: Path,
    *,
    scope: ArtifactScope | None = None,
    agents: tuple[str, ...] = (),
    mappings: tuple[RootMapping, ...] = (),
    environ: Mapping[str, str] | None = None,
) -> Inventory:
    environment = dict(os.environ if environ is None else environ)
    if scope not in {None, "project", "global"}:
        raise LoadoutError("scope must be project or global")
    if set(agents) - KNOWN_HARNESSES or len(agents) != len(set(agents)):
        raise LoadoutError("agents must be distinct supported harness names")
    root = root.absolute() if scope == "global" else project_root(root)
    mappings = _validate_mappings(mappings)
    existing = [
        ("project", root / "loadout/config.toml"),
        ("global", root / "loadout.toml"),
        ("global", root / "loadout/loadout.toml"),
    ]
    selected = [(s, p) for s, p in existing if p.is_file() and (scope is None or scope == s)]
    if len(selected) == 1:
        inferred, config = selected[0]
        return Inventory(
            root,
            config.parent,
            "project" if inferred == "project" else "global",
            agents,
            mappings,
            (),
            path_preconditions(config),
            initialized=config,
        )
    issues = []
    if len(selected) > 1:
        issues.append(
            Issue(
                "initialized-conflict",
                tuple(p for _, p in selected),
                "Multiple initialized scopes or global manifests need an explicit selection.",
            )
        )
    if scope is None:
        issues.append(
            Issue(
                "scope-required",
                (root,),
                "Select project or global scope; this uninitialized directory does not establish deployment ownership.",
            )
        )
    source_root = root / "loadout"
    live = (
        live_roots(Path(environment.get("HOME") or Path.home()), environment)
        if scope == "global"
        else ()
    )
    if not agents:
        evidence = (
            live if scope == "global" else _project_mappings(root, tuple(sorted(KNOWN_HARNESSES)))
        )
        agents = tuple(
            dict.fromkeys(
                a for m in (*evidence, *mappings) if _configuration_evidence(m) for a in m.agents
            )
        )
    if not agents:
        issues.append(
            Issue(
                "agents-required",
                (root,),
                "Select the configured harnesses; shared instructions and installed binaries do not establish membership.",
            )
        )
    if scope == "project" and any(not m.destination.is_relative_to(root) for m in mappings):
        raise LoadoutError("project mapping destinations must remain inside the project")
    automatic = (
        tuple(replace(m, agents=tuple(a for a in m.agents if a in agents)) for m in live)
        if scope == "global"
        else _project_mappings(root, agents)
    )
    automatic = tuple(m for m in automatic if m.agents)
    selected_mappings = tuple(dict.fromkeys((*automatic, *mappings)))
    allowed = tuple(
        dict.fromkeys((root.resolve(), *(m.source.absolute() for m in selected_mappings)))
    )
    home = environment.get("HOME")
    builder = _InventoryBuilder(
        DestinationLayout(root, selected_mappings, Path(home) if home else None),
        allowed,
        tuple(
            dict.fromkeys(
                path
                for m in selected_mappings
                if m.kind == "harness"
                for path in (m.source, m.source.resolve())
            )
        ),
    )
    builder.confirmed_roots = tuple(m.source for m in selected_mappings)
    for mapping in selected_mappings:
        builder.walk(mapping)
    if scope != "global":
        for mapping in _instruction_mappings(builder, agents):
            builder.walk(mapping)
    else:
        _global_sources(builder, root, agents, selected_mappings, live)
    _dependencies(builder, scope, environment)
    ignored = ignored_paths(
        c.canonical or c.path for c in builder.candidates.values() if c.disposition == "migrated"
    )
    candidates = tuple(
        replace(
            c,
            private=c.private or (c.canonical or c.path) in ignored,
            personal=c.personal or (c.canonical or c.path) in ignored,
        )
        if c.disposition == "migrated"
        else c
        for c in builder.candidates.values()
    )
    return Inventory(
        root,
        source_root,
        scope,
        agents,
        selected_mappings,
        candidates,
        tuple(builder.states.values()),
        (*issues, *builder.issues),
        destination_environment=tuple(
            (name, environment[name])
            for name in (
                "HOME",
                "CLAUDE_CONFIG_DIR",
                "CODEX_HOME",
                "XDG_CONFIG_HOME",
                "PI_CODING_AGENT_DIR",
                "OPENCODE_CONFIG",
                "OPENCODE_CONFIG_DIR",
            )
            if environment.get(name)
        ),
    )


def _configuration_evidence(mapping: RootMapping) -> bool:
    if mapping.kind == "shared" or len(mapping.agents) != 1:
        return False
    if mapping.kind == "file":
        return mapping.source.is_file()
    if not mapping.source.is_dir():
        return False
    return any(not _excluded(Path(child.name)) for child in mapping.source.iterdir())


def _dependency_strings(value: object, key: str = "") -> tuple[str, ...]:
    if isinstance(value, dict):
        return tuple(
            reference
            for name, child in value.items()
            for reference in _dependency_strings(child, str(name))
        )
    if isinstance(value, list):
        return tuple(reference for child in value for reference in _dependency_strings(child, key))
    if not isinstance(value, str):
        return ()
    if key == "command":
        try:
            return tuple(token for token in shlex.split(value) if _path_token(token))
        except ValueError:
            pass
    if key in {
        "instructions",
        "skills",
        "extensions",
        "packages",
        "path",
        "args",
        "source",
    } and _path_token(value):
        return (value,)
    return ()


def _path_token(value: str) -> bool:
    return "/" in value and "://" not in value and not value.startswith(("-", "@", "npm:", "git:"))


def _references(candidate: Candidate) -> tuple[str, ...]:
    if candidate.format in {"json", "toml"}:
        try:
            document = parse_document(candidate.content.decode(), candidate.format)
            if candidate.document_name == ".claude.json" and "claude" in candidate.agents:
                document = {key: value for key, value in document.items() if key == "mcpServers"}
            elif candidate.document_name == "config.toml" and "codex" in candidate.agents:
                document = {
                    key: value
                    for key, value in document.items()
                    if key not in {"projects", "trust"}
                }
            elif candidate.document_name == "settings.json" and "pi" in candidate.agents:
                document = {
                    key: value for key, value in document.items() if key != "lastChangelogVersion"
                }
            return _dependency_strings(document)
        except (LoadoutError, UnicodeError):
            return ()
    if candidate.path.suffix not in {".sh", ".js", ".ts", ".mjs", ".cjs"}:
        return ()
    return tuple(
        re.findall(
            r"(?:\bfrom\s*|\brequire\(\s*|\bimport\s*|\bsource\s+|(?:^|\n)\.\s+)[\"']?((?:\.{1,2}/|/|~/)[^\s\"';)]+)",
            candidate.content.decode(errors="replace"),
        )
    )


def _dependencies(
    builder: _InventoryBuilder, scope: ArtifactScope | None, environment: Mapping[str, str]
) -> None:
    visited: set[tuple[Path, str]] = set()
    while True:
        pending = [
            (c, reference)
            for c in tuple(builder.candidates.values())
            if c.disposition == "migrated"
            for reference in _references(c)
            if (c.path, reference) not in visited
        ]
        if not pending:
            return
        for candidate, reference in pending:
            visited.add((candidate.path, reference))
            _dependency(builder, candidate, reference, scope, environment)


def _dependency(
    builder: _InventoryBuilder,
    candidate: Candidate,
    reference: str,
    scope: ArtifactScope | None,
    environment: Mapping[str, str],
) -> None:
    expanded = reference.replace("${CLAUDE_PROJECT_DIR}", str(builder.root)).replace(
        "$CLAUDE_PROJECT_DIR", str(builder.root)
    )
    for name, value in environment.items():
        if name in {
            "HOME",
            "CLAUDE_CONFIG_DIR",
            "CODEX_HOME",
            "XDG_CONFIG_HOME",
            "PI_CODING_AGENT_DIR",
        }:
            expanded = expanded.replace("${" + name + "}", value).replace("$" + name, value)
    path = _home_path(expanded, Path(environment.get("HOME") or Path.home()))
    if "$" in expanded or any(character in expanded for character in "*?["):
        builder.issues.append(
            Issue(
                "dependency-mapping",
                (candidate.path,),
                "A dynamic or glob dependency needs an explicit materialized source mapping.",
            )
        )
        return
    bases = (
        (builder.root, candidate.path.parent)
        if scope == "project" and candidate.format in {"json", "toml"}
        else (candidate.path.parent, builder.root)
    )
    targets = (path,) if path.is_absolute() else tuple(base / path for base in bases)
    targets = tuple(Path(os.path.normpath(target)) for target in targets)
    assert candidate.destination is not None
    deployed_bases = (
        (builder.root, candidate.destination.parent)
        if scope == "project" and candidate.format in {"json", "toml"}
        else (candidate.destination.parent, builder.root)
    )
    deployed = (path,) if path.is_absolute() else tuple(base / path for base in deployed_bases)
    deployed = tuple(Path(os.path.normpath(target)) for target in deployed)
    destinations = tuple(builder.layout.normalize(target) for target in deployed)
    originals = tuple(builder.candidates.values())
    for source, destination in zip(targets, destinations, strict=True):
        if any(
            c.path.resolve() == source.resolve()
            and c.destination is not None
            and builder.layout.normalize(c.destination) == destination
            and c.disposition == "migrated"
            for c in originals
        ):
            return
    matched = next(
        (
            c
            for c in originals
            if c.destination is not None
            and builder.layout.normalize(c.destination) in destinations
            and c.disposition == "migrated"
        ),
        None,
    )
    if matched is not None and (path.is_absolute() or not any(t.exists() for t in targets)):
        return
    retained = next(
        (
            c
            for c in originals
            if c.disposition == "migrated"
            and any(c.path.resolve() == target.resolve() for target in targets)
        ),
        None,
    )
    if retained is not None:
        builder.issues.append(
            Issue(
                "dependency-activation",
                (candidate.path, retained.path),
                "This declaration still references an original at a different deployment path; select its active mapping before migration.",
            )
        )
        return
    allowed = tuple(
        target for target in targets if any(target.is_relative_to(root) for root in builder.allowed)
    )
    existing = next(
        (
            target
            for target in allowed
            if target.is_file() or target.is_dir() or target.is_symlink()
        ),
        None,
    )
    if existing is None:
        builder.issues.append(
            Issue(
                "dependency-mapping",
                (candidate.path,),
                "A referenced local dependency is missing or outside the selected roots; supply its source/destination mapping.",
            )
        )
        return
    if existing.is_dir():
        builder.issues.append(
            Issue(
                "dependency-mapping",
                (candidate.path, existing),
                "Confirm the consumer and category of this referenced directory with an explicit root mapping.",
            )
        )
        return
    destination = deployed[targets.index(existing)]
    builder.walk(RootMapping(existing, destination, candidate.agents, "file", "support"))


def _global_sources(
    builder: _InventoryBuilder,
    root: Path,
    agents: tuple[str, ...],
    mappings: tuple[RootMapping, ...],
    live: tuple[RootMapping, ...],
) -> None:
    mapped = {m.source for m in mappings}
    builder.remember(root)
    for agent, layouts in SOURCE_LAYOUTS.items():
        destination = next(
            m.destination for m in live if m.agents == (agent,) and m.kind == "harness"
        )
        for layout in layouts:
            source = root / layout
            if (
                source in mapped
                or any(source.is_relative_to(p) for p in mapped)
                or not source.exists()
            ):
                continue
            builder.walk(RootMapping(source, destination, (agent,)), confirmed=False)
    for child in sorted(root.iterdir()):
        if (
            child in mapped
            or any(child.is_relative_to(p) for p in mapped)
            or child.name in WALK_EXCLUSIONS
            or child.name
            in {".claude", ".codex", ".pi", ".config", "claude", "codex", "opencode", "pi"}
        ):
            continue
        if child.name == ".agents":
            builder.walk(
                RootMapping(
                    child,
                    next(m.destination for m in live if m.kind == "shared"),
                    agents or ("codex", "opencode", "pi"),
                    "shared",
                ),
                confirmed=False,
            )
        elif child.is_dir() and child.name in UNSUPPORTED_HARNESS_ROOTS:
            builder._unresolved(
                child,
                None,
                (),
                "unsupported-harness",
                "This recognizable harness tree has no supported migration mapping; resolve its ownership explicitly.",
            )
        elif child.is_file() and (
            child.suffix in {".sh", ".tmpl"}
            or child.name in INSTRUCTION_FILES
            or child.name in {"settings.json", "config.toml", "install", "Makefile", "justfile"}
        ):
            builder._unresolved(
                child,
                None,
                (),
                "deployment-activation",
                "Retain this installer/template dependency and supply its deployment mapping; discovered code is never executed.",
            )
