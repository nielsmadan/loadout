from __future__ import annotations

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .agents import SliceOutput
from .errors import LoadoutError

PROJECT_DIR = "loadout"
PROJECT_CONFIG_NAME = "config.toml"

KNOWN_HARNESSES = frozenset({"claude", "codex", "opencode", "pi"})


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
    and that is forced by the harnesses rather than chosen: Codex, OpenCode and
    Pi all read a repo-root `AGENTS.md` (reference/config.md), so one path would
    have to hold three orders. With a single order the two generated documents
    are byte-identical by construction — `composition.render` takes no agent
    argument — rather than by an assertion that could fail open.
    """

    harnesses: tuple[str, ...]
    templates: tuple[str, ...] = ()
    vendored: tuple[tuple[str, str], ...] = ()
    instructions: tuple[str, ...] = ()

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

    unknown = sorted(set(data) - {"harnesses", "templates", "template", "instructions"})
    if unknown:
        raise LoadoutError(
            f"{path}: unrecognised key(s) {', '.join(unknown)}; 'harnesses', "
            f"'templates', 'instructions' and [template.<name>] are the keys this "
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
        "mcp": SliceOutput(renderer="claude-mcp", output=".claude/mcp-permissions.json"),
        "instructions": SliceOutput(output="CLAUDE.md"),
    },
    "codex": {
        "permissions": SliceOutput(
            renderer="codex-project", output=".codex/rules/permissions.rules"
        ),
        "instructions": SliceOutput(output="AGENTS.md"),
    },
    "opencode": {
        "permissions": SliceOutput(
            renderer="opencode", output="opencode.json", preserve_foreign=True
        ),
        "instructions": SliceOutput(output="AGENTS.md"),
    },
    "pi": {
        "permissions": SliceOutput(
            renderer="pi-project",
            output=".pi/extensions/pi-permission-system/config.json",
        ),
        # Three agents, one path, on purpose — see ProjectConfig.instructions.
        "instructions": SliceOutput(output="AGENTS.md"),
    },
}

# A template contributes this as one unnamed block, the way it contributes
# permissions.toml: a tier beneath the project, never a fragment the project has
# to name. Adopting a template should bring its instructions without restating
# them.
TEMPLATE_INSTRUCTIONS = "instructions.md"


def project_slices(harnesses: Iterable[str]) -> tuple[tuple[str, str, SliceOutput], ...]:
    """Every (agent, slice, output) these harnesses generate, in preset order."""
    return tuple(
        (harness, name, spec)
        for harness in harnesses
        for name, spec in PROJECT_PRESET[harness].items()
    )


def project_outputs(
    config: ProjectConfig, harnesses: Iterable[str] | None = None
) -> tuple[str, ...]:
    """Every in-repo path this project generates — what `.gitignore` needs.

    The instruction documents are included only when something could produce
    them, because ignoring a file loadout does not generate is worse than not
    ignoring one it does: an untracked hand-written `CLAUDE.md` would become
    impossible to commit, and silently — `init`'s tracked-file note does not
    fire on a file git is not tracking. A declared template counts without being
    resolved, since resolution needs the machine config and can fail for reasons
    a `.gitignore` should not care about.

    Deduplicated because three harnesses share one `AGENTS.md`, and
    order-preserving for the reason `dedupe()` is: never a set().
    """
    selected = config.harnesses if harnesses is None else harnesses
    writes_instructions = bool(config.instructions or config.templates)
    paths = [
        spec.output
        for _, name, spec in project_slices(selected)
        if spec.output is not None and (name != "instructions" or writes_instructions)
    ]
    return tuple(dict.fromkeys(paths))
