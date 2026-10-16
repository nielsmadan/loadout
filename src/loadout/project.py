from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LoadoutError
from .gitignore import escape
from .harnesses import DROID_AGENTS_DIRECTORY, KNOWN_HARNESSES, SliceOutput
from .skill_policy import SkillPolicy, parse_skill_policies

PROJECT_DIR = "loadout"
PROJECT_CONFIG_NAME = "config.toml"


@dataclass(frozen=True)
class ProjectConfig:
    """Which harnesses this project generates configuration for, and which
    templates it opts into.

    Validation lives here, not in load_project_config, so it cannot be bypassed
    by constructing a ProjectConfig directly (as init_project used to) — see the
    milestone 4 fix-wave note on the duplicate-harness defect this closed.

    `vendored` is a tuple of `(name, hash)` pairs rather than a mapping because
    the dataclass is frozen and a mapping is not hashable.

    `instructions` is **one order for the whole project, not one per harness**,
    and that is forced by the harnesses rather than chosen: Codex, Droid,
    OpenCode and Pi all read a repo-root `AGENTS.md` (reference/config.md), so
    one path would have to hold four orders. Beside another harness, Claude's
    `CLAUDE.md` imports that `AGENTS.md` rather than repeating it.
    """

    harnesses: tuple[str, ...]
    templates: tuple[str, ...] = ()
    vendored: tuple[tuple[str, str], ...] = ()
    instructions: tuple[str, ...] = ()
    skill_policies: tuple[SkillPolicy, ...] = ()

    def __post_init__(self) -> None:
        if not self.harnesses:
            raise LoadoutError("at least one harness is required")
        if len(set(self.harnesses)) != len(self.harnesses):
            raise LoadoutError("duplicate harness in the list")
        bad = sorted(set(self.harnesses) - KNOWN_HARNESSES)
        if bad:
            known = ", ".join(sorted(KNOWN_HARNESSES))
            raise LoadoutError(f"unknown harness(es) {', '.join(bad)} (known: {known})")
        if len(set(self.templates)) != len(self.templates):
            raise LoadoutError("duplicate template in the list")
        if len(set(self.instructions)) != len(self.instructions):
            raise LoadoutError("duplicate instruction fragment in the list")
        _validate_skill_policies(self.skill_policies, self.harnesses)
        orphan = sorted({name for name, _ in self.vendored} - set(self.templates))
        if orphan:
            raise LoadoutError(
                f"[template.{orphan[0]}] records provenance for a template this project "
                f"does not declare; add it to `templates` or delete the block"
            )

    def vendored_hash(self, name: str) -> str | None:
        for recorded, digest in self.vendored:
            if recorded == name:
                return digest
        return None


def _validate_skill_policies(policies: tuple[SkillPolicy, ...], harnesses: tuple[str, ...]) -> None:
    for policy in policies:
        extra = set(policy.agents) - set(harnesses)
        if extra:
            raise LoadoutError(
                f"skills.{policy.name} names unconfigured harness(es) {', '.join(sorted(extra))}"
            )


def project_config_path(root: Path) -> Path:
    return root / PROJECT_DIR / PROJECT_CONFIG_NAME


def load_project_config(path: Path) -> ProjectConfig:
    if not path.is_file():
        raise LoadoutError(f"project config not found: {path}")
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error

    unknown = sorted(
        set(data)
        - {
            "harnesses",
            "templates",
            "template",
            "instructions",
            "skills",
        }
    )
    if unknown:
        raise LoadoutError(
            f"{path}: unrecognised key(s) {', '.join(unknown)}; 'harnesses', "
            f"'templates', 'instructions', 'skills' and [template.<name>] are the keys this "
            f"file accepts"
        )

    raw = data.get("harnesses")
    if not isinstance(raw, list) or not all(isinstance(h, str) for h in raw):
        raise LoadoutError(f"{path}: harnesses must be a list of strings")

    raw_templates = data.get("templates", [])
    if not isinstance(raw_templates, list) or not all(
        isinstance(t, str) and t for t in raw_templates
    ):
        raise LoadoutError(f"{path}: templates must be a list of non-empty strings")

    raw_instructions = data.get("instructions", [])
    if not isinstance(raw_instructions, list) or not all(
        isinstance(t, str) and t for t in raw_instructions
    ):
        raise LoadoutError(f"{path}: instructions must be a list of non-empty strings")

    try:
        return ProjectConfig(
            harnesses=tuple(raw),
            templates=tuple(raw_templates),
            vendored=_parse_provenance(data.get("template", {}), path),
            instructions=tuple(raw_instructions),
            skill_policies=parse_skill_policies(data.get("skills")),
        )
    except LoadoutError as error:
        raise LoadoutError(f"{path}: {error}") from error


def _parse_provenance(raw: object, path: Path) -> tuple[tuple[str, str], ...]:
    """`[template.<name>] vendored = "<hash>"` — the content hash of a vendored copy.

    Source rather than generated output, so ADR 0008's prohibition on stamping a
    hash does not reach it: that rule keeps *generated* content a pure function of
    the source, and a hash recorded in the source is the source.
    """
    if not isinstance(raw, dict):
        raise LoadoutError(f"{path}: [template.<name>] must be a table per template")
    provenance: list[tuple[str, str]] = []
    for name, block in raw.items():
        if not isinstance(block, dict):
            raise LoadoutError(f"{path}: [template.{name}] must be a table")
        stray = sorted(set(block) - {"vendored"})
        if stray:
            raise LoadoutError(
                f"{path}: [template.{name}] has unrecognised key(s) {', '.join(stray)}; "
                f"'vendored' is the only key it accepts — a template is referenced by "
                f"name, never by path"
            )
        digest = block.get("vendored")
        if not isinstance(digest, str) or not digest:
            raise LoadoutError(f"{path}: [template.{name}] vendored must be a non-empty string")
        provenance.append((name, digest))
    return tuple(provenance)


# The project-scope twin of GLOBAL_PRESET: same type, separate table. Separate
# because a destination here is a path inside the repo, and project scope must
# never name a machine path — a path in a committed file is wrong for everyone
# who is not its author, which is the same reason project scope carries no
# [[source]] list (see templates.py:declared_sources). So every entry sets
# `output` and none sets `destination`; `test_project_preset` pins that.
#
# Three renderers are project-specific and must stay that way: they differ from
# their global namesakes only in emitted key order (PROJECT_CATEGORIES), which
# reads as duplication and is not — see ADR 0006 and renderers.py.
PROJECT_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "permissions": SliceOutput(
            renderer="claude-project",
            output=".claude/settings.json",
            preserve_foreign=True,
        ),
        "mcp-permissions": SliceOutput(
            renderer="claude-mcp-permissions", output=".claude/mcp-permissions.json"
        ),
        # A whole file of its own — .mcp.json is Claude's project MCP config,
        # never shared with settings.json or mcp-permissions.json.
        "mcp": SliceOutput(renderer="claude-project-servers", output=".mcp.json"),
        "instructions": SliceOutput(output="CLAUDE.md"),
        "skills": SliceOutput(output=".claude/skills"),
        "agents": SliceOutput(output=".claude/agents"),
    },
    "codex": {
        "permissions": SliceOutput(
            renderer="codex-project", output=".codex/rules/permissions.rules"
        ),
        "instructions": SliceOutput(output="AGENTS.md"),
        "skills": SliceOutput(output=".agents/skills"),
        "agents": SliceOutput(output=".codex/agents"),
        # No mcp entry either: whether [mcp_servers.*] survives Codex's
        # project-config filter is unverified, and its own warning says
        # unsupported project-local keys are ignored. See docs/reference/servers.md.
    },
    "droid": {
        "permissions": SliceOutput(
            renderer="droid", output=".factory/settings.json", preserve_foreign=True
        ),
        "hooks": SliceOutput(
            renderer="droid-hooks",
            output=".factory/hooks.json",
            source_slice="hooks",
            owned_key="hooks",
        ),
        "plugins": SliceOutput(
            renderer="droid-plugins",
            output=".factory/settings.json",
            source_slice="plugins",
        ),
        "mcp": SliceOutput(renderer="droid-servers", output=".factory/mcp.json"),
        "instructions": SliceOutput(output="AGENTS.md"),
        "skills": SliceOutput(output=".factory/skills"),
        "agents": SliceOutput(output=f".factory/{DROID_AGENTS_DIRECTORY}"),
    },
    "opencode": {
        "permissions": SliceOutput(
            renderer="opencode", output="opencode.json", preserve_foreign=True
        ),
        # One key of the same document `permissions` writes — see
        # compose_permission_document's grouping in emit.py:render_project.
        "mcp": SliceOutput(renderer="opencode-servers", output="opencode.json", owned_key="mcp"),
        "instructions": SliceOutput(output="AGENTS.md"),
        # Its own directory, never `.claude/skills` — OpenCode reads both, and a
        # skill written twice under one name is resolved by a race rather than by
        # precedence. See reference/opencode.md; the env flag is required setup.
        "skills": SliceOutput(output=".opencode/skills"),
        "agents": SliceOutput(output=".opencode/agents"),
    },
    "pi": {
        "permissions": SliceOutput(
            renderer="pi-project",
            output=".pi/extensions/pi-permission-system/config.json",
        ),
        # Three agents, one path, on purpose — see ProjectConfig.instructions.
        "instructions": SliceOutput(output="AGENTS.md"),
        "skills": SliceOutput(output=".pi/skills"),
        "agents": SliceOutput(output=".pi/agents"),
        # No mcp entry: pi-mcp-adapter's shipped README calls .mcp.json the
        # "Preferred project config" and reads it directly, so writing
        # .pi/mcp.json too would hand Pi the same servers twice under two names.
    },
}


def project_slices(harnesses: Iterable[str]) -> tuple[tuple[str, str, SliceOutput], ...]:
    """Every (agent, slice, output) these harnesses generate, in preset order."""
    return tuple(
        (harness, name, spec)
        for harness in harnesses
        for name, spec in PROJECT_PRESET[harness].items()
    )


def refuse_escaping(root: Path, paths: Iterable[Path], outputs: Iterable[Path] = ()) -> None:
    """Project scope writes only to real paths inside the project, never under the git
    dir or the source. A link committed to the repository can point anywhere, even
    inside it, so no link may sit between the root and a path. The one exception is a
    leaf link whose target is itself one of this render's `outputs` (CLAUDE.md ->
    AGENTS.md)."""
    boundary = root.resolve()
    written = frozenset(
        relative for path in outputs if (relative := _within(path, root, boundary)) is not None
    )
    problems = [problem for path in paths if (problem := _unsafe(path, root, boundary, written))]
    if problems:
        raise LoadoutError(
            f"refusing to write {'; '.join(problems)}. Replace the link with a real file or "
            "folder, or remove it"
        )


# Never written, whatever a link says: git runs what is under its directory, and the
# outputs are rendered from the source.
PROTECTED = frozenset({".git", PROJECT_DIR})


def _within(path: Path, root: Path, boundary: Path) -> PurePosixPath | None:
    for base in (root, boundary):
        if path.is_relative_to(base):
            return PurePosixPath(path.relative_to(base).as_posix())
    return None


def _outside(target: Path, boundary: Path) -> str:
    return "" if target.is_relative_to(boundary) else ", outside the project"


def _unsafe(
    path: Path, root: Path, boundary: Path, written: frozenset[PurePosixPath]
) -> str | None:
    relative = _within(path, root, boundary)
    if relative is None:
        return f"{path}: outside the project"
    # Case-folded: macOS and Windows file systems treat .GIT as .git.
    if relative.parts and relative.parts[0].casefold() in PROTECTED:
        return f"{path}: under {relative.parts[0]}/, which loadout never writes"
    for depth in range(1, len(relative.parts)):
        link = root / Path(*relative.parts[:depth])
        if link.is_symlink():
            target = link.resolve()
            return f"{path}: through the link {link} -> {target}{_outside(target, boundary)}"
    leaf = root / Path(relative)
    if not leaf.is_symlink():
        return None
    target = leaf.resolve()
    if target.is_relative_to(boundary) and _within(target, root, boundary) in written:
        return None
    return f"{leaf} -> {target}, a link to a file this sync does not write" + _outside(
        target, boundary
    )


def refuse_external_links(root: Path, excluded: frozenset[str] = frozenset()) -> None:
    """Project inputs are the repository's own: anything in `loadout/` that a link leads
    out of the project, or into its git dir, would be read into committed output. Links
    are followed, so a linked folder that lands elsewhere inside the project is checked
    for what it holds; a folder in `excluded` inside a skill's own tree is never read,
    so it is not entered."""
    boundary = root.resolve()
    escaping: list[tuple[Path, Path]] = []
    visited: set[Path] = set()
    source = root / PROJECT_DIR
    # Each path with where it lands and whether it is a folder. Only a link is resolved:
    # anything else lands beside its folder's own landing place.
    pending = [(source, source.resolve(), source.is_dir())]
    while pending:
        current, resolved, directory = pending.pop()
        if not resolved.is_relative_to(boundary) or _in_git_dir(resolved, boundary):
            escaping.append((current, resolved))
            continue
        if not directory or resolved in visited:
            continue
        visited.add(resolved)
        with os.scandir(current) as listing:
            entries = sorted(listing, key=lambda entry: entry.name, reverse=True)
        for entry in entries:
            path = current / entry.name
            if entry.name in excluded and _in_a_skill(path.relative_to(source).parts):
                continue
            if entry.is_symlink():
                target = path.resolve()
                pending.append((path, target, target.is_dir()))
            else:
                pending.append((path, resolved / entry.name, entry.is_dir(follow_symlinks=False)))
    if escaping:
        listed = "; ".join(f"{link} -> {target}" for link, target in escaping)
        raise LoadoutError(
            f"refusing to read through a link out of the project or into .git: {listed}. Copy "
            "the file into the repository, or point the link inside it"
        )


def _in_git_dir(path: Path, boundary: Path) -> bool:
    """Under the repository's git dir, which holds credentials and runs hooks."""
    parts = path.relative_to(boundary).parts
    return bool(parts) and parts[0].casefold() == ".git"


def _in_a_skill(parts: tuple[str, ...]) -> bool:
    """Below `skills/<name>/`, a project's own or a template's: the skill's name itself
    is not inside its tree, so a skill named like an excluded folder is still read."""
    return any(part == "skills" and len(parts) > index + 2 for index, part in enumerate(parts))


def gitignore_entries(root: Path, written: Iterable[Path]) -> tuple[str, ...]:
    """The root-anchored `.gitignore` lines for what sync wrote, and nothing else.

    A skill's files collapse to its folder, never the skills directory, because
    loadout owns only the folders it writes: a skill installed there by hand has
    to stay visible to git beside them. Every harness's skills directory counts, so
    a retired harness's skill folder maps to the entry that was added for it.
    """
    skill_dirs = tuple(
        PurePosixPath(spec.output)
        for _, name, spec in project_slices(PROJECT_PRESET)
        if name == "skills" and spec.output is not None
    )

    def entry(path: Path) -> str:
        relative = PurePosixPath(path.relative_to(root).as_posix())
        for directory in skill_dirs:
            if relative.is_relative_to(directory) and relative != directory:
                return escape(f"/{directory}/{relative.relative_to(directory).parts[0]}/")
        return escape(f"/{relative}")

    return tuple(dict.fromkeys(entry(path) for path in written if path.is_relative_to(root)))


def project_outputs(
    config: ProjectConfig, harnesses: Iterable[str] | None = None
) -> tuple[str, ...]:
    """Every in-repo path this project's presets can generate, folders included: what
    `check --staged` treats as generated, and what the early link check allows as a
    link's target.

    The instruction documents count only when something could produce them, so a
    hand-written `CLAUDE.md` in a project with no instructions stays the user's. A
    declared template counts without being resolved, since resolution needs the
    machine config and can fail for reasons neither caller should care about.

    Deduplicated because four harnesses share one `AGENTS.md`, and
    order-preserving for the reason `dedupe()` is: never a set().
    """
    selected = tuple(config.harnesses if harnesses is None else harnesses)
    writes_instructions = bool(config.instructions or config.templates)
    paths = [
        spec.output
        for _, name, spec in project_slices(selected)
        if spec.output is not None and (name != "instructions" or writes_instructions)
    ]
    return tuple(dict.fromkeys(paths))
