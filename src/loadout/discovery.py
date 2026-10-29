from __future__ import annotations

import os
import re
import shlex
import stat
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import git_runner
from .containment import in_git_dir
from .errors import LoadoutError
from .git_privacy import ignored_paths, tracked_paths
from .harnesses import DROID_AGENTS_DIRECTORY, GLOBAL_PRESET, KNOWN_HARNESSES, SHARED_SKILL_READERS
from .manifest import manifest_path
from .migration_models import Candidate, EntryState, Inventory, Issue, IssueCode, RootMapping
from .migration_paths import DestinationLayout
from .native_documents import jsonc_to_json, parse_document
from .output import Scope, entry_path, sha256
from .project import PROJECT_DIR, PROJECT_PRESET, project_config_path
from .runtime_paths import (
    HARNESS_RUNTIME_ENTRIES,
    RUNTIME_DIRECTORIES,
    RUNTIME_FILES,
    UNMANAGED_NAMES,
)

# The harnesses whose project instructions are AGENTS.md, from the preset.
AGENTS_MD_READERS = tuple(
    harness
    for harness, slices in PROJECT_PRESET.items()
    if (spec := slices.get("instructions")) is not None and spec.output == "AGENTS.md"
)

GLOBAL_ROOTS = {
    "claude": ("CLAUDE_CONFIG_DIR", ".claude"),
    "codex": ("CODEX_HOME", ".codex"),
    "droid": ("FACTORY_HOME_OVERRIDE", ".factory"),
    "opencode": ("XDG_CONFIG_HOME", ".config"),
    "pi": ("PI_CODING_AGENT_DIR", ".pi/agent"),
}
# What the destinations depend on, captured at discovery and checked again before applying.
DESTINATION_ENVIRONMENT = (
    "HOME",
    *(name for name, _ in GLOBAL_ROOTS.values()),
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
)
# The global document each harness also writes runtime state into, and that state's keys.
# Claude's `.claude.json` is runtime state apart from `mcpServers`.
RUNTIME_DOCUMENTS = {
    "claude": ".claude.json",
    "codex": "config.toml",
    "droid": "settings.json",
    "pi": "settings.json",
}
# The keys a harness writes for itself: what its preset carries over from the live
# destination, and Codex's own, which no slice of loadout's declares.
RUNTIME_KEYS = {
    "codex": frozenset({"projects", "trust"}),
    **{
        agent: keys
        for agent, slices in GLOBAL_PRESET.items()
        if (keys := frozenset(key for spec in slices.values() for key in spec.preserve))
    },
}
PROJECT_ROOTS = {
    "claude": ".claude",
    "codex": ".codex",
    "droid": ".factory",
    "opencode": ".opencode",
    "pi": ".pi",
}
INSTRUCTION_FILES = frozenset(
    {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CLAUDE.local.md", "opencode.md"}
)
_MODULE_TREES = ("hooks/", "rules/", "scripts/")
# The only paths discovery reads beneath a harness or shared root, per scope and
# harness. A trailing slash names a tree. Everything else under a root is left
# alone unread, which is what lets init adopt a root it does not fully understand.
MANAGED_PATHS: dict[str, dict[str, tuple[str, ...]]] = {
    "global": {
        "claude": (
            "CLAUDE.md",
            "settings.json",
            "mcp-permissions.json",
            "skills/",
            "agents/",
            *_MODULE_TREES,
        ),
        "codex": (
            "AGENTS.md",
            "AGENTS.override.md",
            "config.toml",
            "hooks.json",
            "rules/",
            "skills/",
            "agents/",
        ),
        "droid": (
            "AGENTS.md",
            "settings.json",
            "hooks.json",
            "mcp.json",
            "skills/",
            f"{DROID_AGENTS_DIRECTORY}/",
            "plugins/",
            *_MODULE_TREES,
        ),
        "opencode": (
            "AGENTS.md",
            "opencode.json",
            "opencode.jsonc",
            "skills/",
            "agents/",
            "plugins/",
            # OpenCode scans "{plugin,plugins}/*.{ts,js}" in its config directory.
            "plugin/",
            *_MODULE_TREES,
        ),
        "pi": (
            "AGENTS.md",
            "settings.json",
            "mcp.json",
            "extensions/pi-permission-system/config.json",
            "skills/",
            "agents/",
            "plugins/",
            *_MODULE_TREES,
        ),
        "shared": ("skills/", "mcp.json", "mcp/mcp.json"),
    },
    "project": {
        "claude": ("settings.json", "mcp-permissions.json", "skills/", "agents/"),
        "codex": ("rules/", "agents/"),
        "droid": (
            "settings.json",
            "hooks.json",
            "mcp.json",
            "skills/",
            f"{DROID_AGENTS_DIRECTORY}/",
        ),
        "opencode": ("skills/", "agents/"),
        "pi": ("extensions/pi-permission-system/config.json", "skills/", "agents/"),
        "shared": ("skills/",),
    },
}
AGENT_DIRECTORIES = frozenset({"agents", DROID_AGENTS_DIRECTORY})
PROJECT_WALK_EXCLUSIONS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        "vendor",
        "dist",
        "build",
        PROJECT_DIR,
    }
)


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
            sha256(entries) if entries is not None else None,
            device=value.st_dev,
            inode=value.st_ino,
        )
    if stat.S_ISREG(value.st_mode):
        return EntryState(path, "file", mode, sha256(path.read_bytes()) if read else None)
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
        if agent == "droid" and value:
            root /= ".factory"
        if agent == "opencode":
            root /= "opencode"
        if not root.is_absolute() or ".." in root.parts:
            raise LoadoutError(f"{variable} must identify an absolute harness root")
        template = (
            "${FACTORY_HOME_OVERRIDE:-~}/.factory"
            if agent == "droid"
            else f"${{{variable}:-~/{fallback}}}" + ("/opencode" if agent == "opencode" else "")
        )
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
            SHARED_SKILL_READERS,
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
        if project_config_path(parent).is_file() or (parent / ".git").exists():
            return parent
    return path


def initialized_project_path(path: Path) -> Path | None:
    config = project_config_path(project_root(path))
    return config if config.is_file() else None


def _home_path(value: str, home: Path) -> Path:
    return home / value[2:] if value.startswith("~/") else home if value == "~" else Path(value)


def _excluded(path: Path) -> bool:
    if not path.parts:
        return False
    if path.parts[0] in RUNTIME_DIRECTORIES:
        return True
    if path.parts[:2] in {("plugins", "cache"), ("plugins", "marketplaces")}:
        return True
    return path.name in RUNTIME_FILES or (
        len(path.parts) == 1 and path.suffix in {".db", ".sqlite", ".sqlite3"}
    )


def _harness_runtime(relative: Path, mapping: RootMapping) -> bool:
    return (
        mapping.destination_template is not None
        and f"{mapping.destination_template}/{relative.as_posix()}" in HARNESS_RUNTIME_ENTRIES
    )


def _private(path: Path) -> bool:
    return ".local." in path.name or path.name.endswith(".local")


def _category(relative: Path, agent: str) -> str | None:
    first = relative.parts[0]
    if first in AGENT_DIRECTORIES and len(relative.parts) > 1:
        return "agents"
    if relative.name in INSTRUCTION_FILES or first == "commands":
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


def managed_paths(scope: str, mapping: RootMapping) -> tuple[str, ...] | None:
    """The allowlist for a root, or None for a file mapping, which names its one file."""
    if mapping.kind == "file":
        return None
    key = "shared" if mapping.kind == "shared" else mapping.harnesses[0]
    return MANAGED_PATHS[scope][key]


def _managed(relative: Path, entries: tuple[str, ...], *, directory: bool) -> bool:
    if relative.name in UNMANAGED_NAMES:
        return False
    if not relative.parts:
        return True
    for entry in entries:
        path = Path(entry)
        if relative == path or (entry.endswith("/") and relative.is_relative_to(path)):
            return True
        if directory and path.is_relative_to(relative):
            return True
    return False


CLAUDE_LOCAL_SETTINGS = (
    "Claude's personal settings for this repository, which loadout does not render and Claude "
    "still applies; /loadout can move its permissions into loadout/permissions.local.toml"
)
SKILLS_FOLDER = "skills"
SHARED_SKILLS = ".agents/skills"
LINKED_SKILLS = "linked-skills"
RETIRED_SKILLS_LINK = (
    "Its harness reads the folder this links to itself, so the link is retired and the "
    "folder stays."
)
RETIRED_SKILL_LINK = (
    "It links to the same skill in another harness's skills folder, which init adopts, so the "
    "link is retired and the skill is rendered here."
)
# The harnesses whose skills loadout renders into the shared `.agents/skills`, per scope.
SHARED_SKILL_WRITERS = {
    "global": frozenset(
        harness
        for harness, slices in GLOBAL_PRESET.items()
        if "skills" in slices and slices["skills"].destination == "~/.agents/skills"
    ),
    "project": frozenset(
        harness
        for harness, slices in PROJECT_PRESET.items()
        if "skills" in slices and slices["skills"].output == ".agents/skills"
    ),
}


def _agents_for(relative: Path, mapping: RootMapping, scope: str) -> tuple[str, ...]:
    """A shared file is adopted only for a harness loadout renders it back for; the
    others read it where it is, so it stays there."""
    if mapping.kind != "shared":
        return mapping.harnesses
    supported = {
        "skills": SHARED_SKILL_WRITERS[scope],
        "plugins": {"codex"},
        "mcp.json": {"pi"},
        "mcp": {"pi"},
    }
    return tuple(a for a in mapping.harnesses if a in supported.get(relative.parts[0], set()))


def _skill_readers(mapping: RootMapping) -> tuple[str, ...]:
    if mapping.kind == "shared":
        return tuple(h for h in mapping.harnesses if h in SHARED_SKILL_READERS)
    return mapping.harnesses


class _InventoryBuilder:
    def __init__(
        self,
        layout: DestinationLayout,
        allowed: tuple[Path, ...],
        runtime_roots: tuple[Path, ...],
        *,
        scope: str,
    ) -> None:
        self.root = layout.root
        self.layout = layout
        self.allowed = allowed
        self.scope = scope
        # A project's harness root linked outside the repository would pull external
        # content into committed source; a global root is de-linked at adoption instead.
        self.confined = scope != "global"
        self.runtime_roots = runtime_roots
        self.candidates: dict[tuple[Path, Path | None], Candidate] = {}
        self.states: dict[Path, EntryState] = {}
        self.issues: list[Issue] = []

    def remember(self, path: Path, *, read: bool = True) -> None:
        for state in path_preconditions(path, read=read):
            previous = self.states.get(state.path)
            if previous is None or previous.digest is None:
                self.states[state.path] = state

    def walk(self, mapping: RootMapping) -> None:
        self._walk(mapping.source, mapping, ())

    def _walk(self, path: Path, mapping: RootMapping, ancestors: tuple[Path, ...]) -> None:
        relative = path.relative_to(mapping.source) if mapping.kind != "file" else Path(path.name)
        entries = managed_paths(self.scope, mapping)
        if self._claude_local_settings(path, relative, mapping) or (
            entries is not None and not _managed(relative, entries, directory=path.is_dir())
        ):
            return
        if not path.exists() and not path.is_symlink():
            self.remember(path, read=False)
            return
        destination = (
            mapping.destination / relative if mapping.kind != "file" else mapping.destination
        )
        try:
            canonical = path.resolve(strict=True)
        except (OSError, RuntimeError):
            self._unresolved(
                path,
                destination,
                mapping.harnesses,
                "symlink-target",
                "Resolve the missing or cyclic symlink target.",
            )
            return
        canonical_excluded = any(
            _excluded(canonical.relative_to(root))
            for root in self.runtime_roots
            if canonical.is_relative_to(root)
        )
        if _excluded(relative) or canonical_excluded or _harness_runtime(relative, mapping):
            self.remember(path, read=False)
            self.candidates[path, destination] = Candidate(
                path,
                canonical,
                destination,
                mapping.harnesses,
                "runtime",
                "runtime-private exclusion",
                "Runtime, authentication or cache path; left in place.",
                private=True,
            )
            return
        self.remember(path, read=False)
        if self.confined and self._stops_at_link(path, canonical, destination, mapping):
            return
        self.remember(canonical, read=False)
        if canonical in ancestors:
            self._unresolved(
                path,
                destination,
                mapping.harnesses,
                "symlink-cycle",
                "Resolve the cyclic directory mapping.",
            )
            return
        if path.is_dir():
            self.remember(path)
            self.remember(canonical)
            for child in sorted(path.iterdir()):
                self._walk(child, mapping, (*ancestors, canonical))
        else:
            self._file(path, canonical, destination, mapping)

    def _claude_local_settings(self, path: Path, relative: Path, mapping: RootMapping) -> bool:
        """A project's personal Claude settings, kept in place and named in the preview."""
        if (
            self.scope != "project"
            or mapping.harnesses != ("claude",)
            or relative != Path("settings.local.json")
            or not path.is_file()
        ):
            return False
        self.remember(path, read=False)
        self.candidates[path, None] = Candidate(
            path,
            path.resolve(),
            None,
            mapping.harnesses,
            "settings",
            "retained dependency",
            CLAUDE_LOCAL_SETTINGS,
        )
        return True

    def _stops_at_link(
        self, path: Path, canonical: Path, destination: Path, mapping: RootMapping
    ) -> bool:
        """Whether a project walk stops at `path`, recording why: a skills folder link it
        retires, or a link it refuses."""
        shared = self._shared_skills_link(path, canonical, mapping)
        sibling = self._sibling_skill_link(path, canonical, mapping)
        if shared or sibling:
            # The skill a sibling link leads to is adopted on its own, not kept for the link.
            self.candidates[path, destination] = Candidate(
                path,
                None if sibling else canonical,
                destination,
                _skill_readers(mapping) if sibling else mapping.harnesses,
                LINKED_SKILLS,
                "retained dependency",
                RETIRED_SKILL_LINK if sibling else RETIRED_SKILLS_LINK,
            )
            return True
        if shared is None and (refused := self._refused_link(path, canonical, mapping)):
            code, message = refused
            self._unresolved(path, destination, mapping.harnesses, code, message)
            return True
        return False

    def _sibling_skill_link(self, path: Path, canonical: Path, mapping: RootMapping) -> bool:
        """Whether `path` is a skill folder linking to the same-named skill in another
        skills folder this init reads, so the skill is read once, through its real folder."""
        if (
            not path.is_symlink()
            or mapping.kind == "file"
            or not (canonical / "SKILL.md").is_file()
        ):
            return False
        if path.relative_to(mapping.source).parent != Path(SKILLS_FOLDER):
            return False
        root = self.allowed[0]
        others = self._read_skill_folders() - {root / path.parent.relative_to(self.root)}
        return canonical.name == path.name and canonical.parent in others

    def _read_skill_folders(self) -> frozenset[Path]:
        """The project skills folders this init adopts skills from, as canonical paths."""
        root = self.allowed[0]
        return frozenset(
            root / mapping.source.relative_to(self.root) / SKILLS_FOLDER
            for mapping in self.layout.mappings
            if (
                mapping.kind == "shared" and SHARED_SKILL_WRITERS[self.scope] & {*mapping.harnesses}
            )
            or (
                mapping.kind == "harness"
                and _managed(
                    Path(SKILLS_FOLDER),
                    managed_paths(self.scope, mapping) or (),
                    directory=True,
                )
            )
        )

    def _shared_skills_link(self, path: Path, canonical: Path, mapping: RootMapping) -> bool | None:
        """For a project harness skills folder that links to a real folder in the same
        repository, outside `.git`: whether the harness reads that folder itself, so the
        link is retired, or not, so init reads through it and replaces it with a real
        folder. None for every other path, which `_refused_link` judges."""
        root = self.allowed[0]
        if not (
            path.is_symlink()
            and mapping.kind == "harness"
            and path.relative_to(mapping.source) == Path(SKILLS_FOLDER)
            and canonical.is_dir()
            and canonical.is_relative_to(root)
            and not in_git_dir(root, canonical)
        ):
            return None
        return canonical == root / SHARED_SKILLS and mapping.harnesses[0] in SHARED_SKILL_READERS

    def _refused_link(
        self, path: Path, canonical: Path, mapping: RootMapping
    ) -> tuple[IssueCode, str] | None:
        """A link a project cannot adopt, reported rather than walked: one out of the
        repository, and, since project sync would refuse it (review 3, 1.3), any link into
        `.git` and any harness folder that is a link or holds one, which sync never writes
        through."""
        if not any(canonical.is_relative_to(root) for root in self.allowed):
            return "external-symlink", (
                f"{path} links to {canonical}, outside the repository, so adopting it would copy "
                "that content into committed project source. Share it as a loadout template "
                f"instead, or replace the link with a real copy (cp -RL {path} <copy>, then move "
                "the copy into its place) and run init again."
            )
        if not path.is_symlink():
            return None
        if in_git_dir(self.allowed[0], canonical):
            return "git-link", (
                f"{path} links to {canonical}, inside the repository's .git, which holds "
                "credentials and runs hooks; remove the link, then run init again."
            )
        if mapping.kind == "file":
            return None
        folder = canonical.is_dir()
        code: IssueCode = "linked-folder" if folder else "linked-file"
        kind = "folder" if folder else "file"
        return code, (
            f"{path} links to the {kind} {canonical}; sync never writes through a linked "
            f"harness folder or a link inside one, so init does not read it. Replace the link "
            f"with a real copy "
            f"(cp -RL {path} <copy>, then move the copy into its place) and run init again."
        )

    def _file(
        self,
        path: Path,
        canonical: Path,
        destination: Path,
        mapping: RootMapping,
    ) -> None:
        relative = path.relative_to(mapping.source) if mapping.kind != "file" else Path(path.name)
        agents = _agents_for(relative, mapping, self.scope)
        category = mapping.category or _category(relative, agents[0] if agents else "")
        if not path.is_file() or not agents or category is None:
            return
        self.remember(path)
        self.remember(canonical)
        if path.name == ".gitkeep":
            self.candidates[path, destination] = Candidate(
                path,
                canonical,
                destination,
                mapping.harnesses,
                "support",
                "retained dependency",
                "Empty-directory tracking marker remains at its original path.",
            )
            return
        content = path.read_bytes()
        if path.suffix == ".jsonc":
            self.remember(path.with_suffix(".json"), read=False)
            if path.with_suffix(".json").exists():
                message = (
                    f"OpenCode reads both opencode.json and opencode.jsonc in {path.parent} and "
                    "merges them, the .jsonc's keys winning; combine them into one file, then "
                    "run init again."
                )
                self.candidates[path, destination] = Candidate(
                    path, None, destination, agents, "support", "unresolved", message
                )
                self.issues.append(
                    Issue("opencode-both-formats", (path, path.with_suffix(".json")), message)
                )
                return
            content = jsonc_to_json(content.decode()).encode()
            destination = destination.with_suffix(".json")
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
            _private(path),
            format_name,
            "its name marks it personal (.local)" if _private(path) else "",
        )

    def _unresolved(
        self,
        path: Path,
        destination: Path | None,
        agents: tuple[str, ...],
        code: IssueCode,
        message: str,
    ) -> None:
        self.remember(path, read=False)
        self.candidates[path, destination] = Candidate(
            path, None, destination, agents, "support", "unresolved", message
        )
        self.issues.append(Issue(code, (path,), message))


def _privacy(
    c: Candidate, ignored: frozenset[Path], settings: frozenset[Path], published: frozenset[Path]
) -> Candidate:
    if c.path in published:
        return replace(c, private=False, private_reason="")
    reason = (
        c.private_reason
        or ("Git ignores it" if (c.canonical or c.path) in ignored else "")
        or ("Git does not track it" if c.path in settings else "")
    )
    return replace(c, private=bool(reason), private_reason=reason)


def _co_owned_settings(
    builder: _InventoryBuilder, root: Path, agents: tuple[str, ...], scope: str | None
) -> frozenset[Path]:
    """Project settings files loadout shares with the harness. One Git tracks is in the
    team's history, so adopting it into committed source publishes nothing new; one it does
    not track stays on this machine (ADR 0030)."""
    if scope == "global":
        return frozenset()
    co_owned = {
        root / str(spec.output)
        for agent in agents
        if (spec := PROJECT_PRESET[agent].get("permissions")) is not None and spec.preserve_foreign
    }
    return frozenset(
        c.path
        for c in builder.candidates.values()
        if c.disposition == "migrated" and c.destination in co_owned
    )


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
    result = git_runner.run(root, "ls-files", "-z", "--cached")
    if result.returncode == 0:
        paths.update(
            root / os.fsdecode(name)
            for name in result.stdout.split(b"\0")
            if name
            and Path(os.fsdecode(name)).name in INSTRUCTION_FILES
            and PROJECT_DIR not in Path(os.fsdecode(name)).parts
        )
    mappings = []
    for path in sorted(paths):
        name = path.name
        consumers = (
            ("claude", "opencode")
            if name.startswith("CLAUDE")
            else ("opencode",)
            if name == "opencode.md"
            else AGENTS_MD_READERS
        )
        active = tuple(a for a in consumers if a in agents)
        mappings.append(RootMapping(path, path, active, "file", "instructions"))
    return tuple(mappings)


def discover(
    root: Path,
    *,
    scope: Scope | None = None,
    harnesses: tuple[str, ...] = (),
    environ: Mapping[str, str] | None = None,
) -> Inventory:
    environment = dict(os.environ if environ is None else environ)
    # One home for the live roots, the layout and the recorded environment, which the
    # later stages rebuild the layout from.
    environment["HOME"] = environment.get("HOME") or str(Path.home())
    if scope not in {None, "project", "global"}:
        raise LoadoutError("scope must be project or global")
    if set(harnesses) - KNOWN_HARNESSES or len(harnesses) != len(set(harnesses)):
        raise LoadoutError("harnesses must be distinct supported harness names")
    root = root.absolute() if scope == "global" else project_root(root)
    selected = _initialized_configs(root, scope)
    if len(selected) == 1:
        inferred, config = selected[0]
        return Inventory(
            root,
            config.parent,
            "project" if inferred == "project" else "global",
            harnesses,
            (),
            (),
            path_preconditions(config),
            initialized=config,
        )
    issues = _selection_issues(root, scope, selected)
    home = Path(environment["HOME"])
    live = live_roots(home, environment) if scope == "global" else ()
    harnesses = harnesses or _evident_harnesses(root, scope, live)
    if not harnesses:
        issues.append(
            Issue(
                "harnesses-required",
                (root,),
                "Select the configured harnesses with --harness NAME, once for each; shared instructions and installed binaries do not establish membership.",
            )
        )
    automatic = (
        tuple(replace(m, harnesses=tuple(a for a in m.harnesses if a in harnesses)) for m in live)
        if scope == "global"
        else _project_mappings(root, harnesses)
    )
    selected_mappings = tuple(m for m in automatic if m.harnesses)
    builder = _builder(root, selected_mappings, home, scope)
    for mapping in selected_mappings:
        builder.walk(mapping)
    if scope != "global":
        for mapping in _instruction_mappings(builder, harnesses):
            builder.walk(mapping)
    else:
        builder.remember(root)
    _dependencies(builder, scope, environment)
    ignored = ignored_paths(
        c.canonical or c.path for c in builder.candidates.values() if c.disposition == "migrated"
    )
    settings = _co_owned_settings(builder, root, harnesses, scope)
    published = tracked_paths(settings)
    candidates = tuple(
        _privacy(c, ignored, settings, published) if c.disposition == "migrated" else c
        for c in builder.candidates.values()
    )
    return Inventory(
        root,
        root / PROJECT_DIR,
        scope,
        harnesses,
        selected_mappings,
        candidates,
        tuple(builder.states.values()),
        (*issues, *builder.issues),
        destination_environment=tuple(
            (name, environment[name]) for name in DESTINATION_ENVIRONMENT if environment.get(name)
        ),
    )


def _initialized_configs(root: Path, scope: Scope | None) -> list[tuple[str, Path]]:
    """The configs at `root` that mark it initialized, for the scope asked for."""
    project = initialized_project_path(root) if scope != "global" else None
    existing = [
        *((("project", project),) if project is not None else ()),
        ("global", manifest_path(root)),
        ("global", manifest_path(root / PROJECT_DIR)),
    ]
    return [(s, p) for s, p in existing if p.is_file() and (scope is None or scope == s)]


def _selection_issues(
    root: Path, scope: Scope | None, selected: list[tuple[str, Path]]
) -> list[Issue]:
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
    return issues


def _evident_harnesses(
    root: Path, scope: Scope | None, live: tuple[RootMapping, ...]
) -> tuple[str, ...]:
    """The harnesses whose own configuration is present, when none were named."""
    evidence = (
        live if scope == "global" else _project_mappings(root, tuple(sorted(KNOWN_HARNESSES)))
    )
    return tuple(
        dict.fromkeys(a for m in evidence if _configuration_evidence(m) for a in m.harnesses)
    )


def _builder(
    root: Path, mappings: tuple[RootMapping, ...], home: Path, scope: Scope | None
) -> _InventoryBuilder:
    allowed = tuple(
        dict.fromkeys(
            (
                root.resolve(),
                *(m.source.absolute() for m in mappings),
                *(entry_path(m.source) for m in mappings),
            )
        )
    )
    return _InventoryBuilder(
        DestinationLayout(root, mappings, home),
        allowed,
        tuple(
            dict.fromkeys(
                path
                for m in mappings
                if m.kind == "harness"
                for path in (m.source, m.source.resolve())
            )
        ),
        scope="global" if scope == "global" else "project",
    )


def _configuration_evidence(mapping: RootMapping) -> bool:
    if mapping.kind == "shared" or len(mapping.harnesses) != 1:
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


def authored_part(document: Mapping[str, Any], agent: str) -> dict[str, Any]:
    """What a person wrote in a document `agent` also writes runtime state into."""
    if agent == "claude":
        return {key: value for key, value in document.items() if key == "mcpServers"}
    return {
        key: value
        for key, value in document.items()
        if key not in RUNTIME_KEYS.get(agent, frozenset())
    }


def _references(candidate: Candidate) -> tuple[str, ...]:
    if candidate.format in {"json", "toml"}:
        try:
            document = parse_document(candidate.content.decode(), candidate.format)
            owner = next(
                (
                    a
                    for a in candidate.harnesses
                    if RUNTIME_DOCUMENTS.get(a) == candidate.document_name
                ),
                None,
            )
            return _dependency_strings(authored_part(document, owner) if owner else document)
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
    builder: _InventoryBuilder, scope: Scope | None, environment: Mapping[str, str]
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
    scope: Scope | None,
    environment: Mapping[str, str],
) -> None:
    expanded = reference.replace("${CLAUDE_PROJECT_DIR}", str(builder.root)).replace(
        "$CLAUDE_PROJECT_DIR", str(builder.root)
    )
    for name, value in environment.items():
        if name == "HOME" or name in {variable for variable, _ in GLOBAL_ROOTS.values()}:
            expanded = expanded.replace("${" + name + "}", value).replace("$" + name, value)
    path = _home_path(expanded, Path(environment.get("HOME") or Path.home()))
    if "$" in expanded or any(character in expanded for character in "*?["):
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
                "This declaration references an original at a different path from the copy loadout deploys; point it at the deployed path, then run init again.",
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
    # Left in place, the reference keeps working: loadout never moves what it did not adopt.
    if existing is None or existing.is_dir():
        return
    destination = deployed[targets.index(existing)]
    builder.walk(RootMapping(existing, destination, candidate.harnesses, "file", "support"))
