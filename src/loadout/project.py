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
    """

    harnesses: tuple[str, ...]
    templates: tuple[str, ...] = ()
    vendored: tuple[tuple[str, str], ...] = ()

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

    unknown = sorted(set(data) - {"harnesses", "templates", "template"})
    if unknown:
        raise LoadoutError(
            f"{path}: unrecognised key(s) {', '.join(unknown)}; 'harnesses', "
            f"'templates' and [template.<name>] are the keys this file accepts"
        )

    raw = data.get("harnesses")
    if not isinstance(raw, list) or not all(isinstance(h, str) for h in raw):
        raise LoadoutError(f"{path}: harnesses must be a list of strings")

    raw_templates = data.get("templates", [])
    if not isinstance(raw_templates, list) or not all(
        isinstance(t, str) and t for t in raw_templates
    ):
        raise LoadoutError(f"{path}: templates must be a list of non-empty strings")

    try:
        return ProjectConfig(
            harnesses=tuple(raw),
            templates=tuple(raw_templates),
            vendored=_parse_provenance(data.get("template", {}), path),
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
    },
    "codex": {
        "permissions": SliceOutput(
            renderer="codex-project", output=".codex/rules/permissions.rules"
        ),
    },
    "opencode": {
        "permissions": SliceOutput(
            renderer="opencode", output="opencode.json", preserve_foreign=True
        ),
    },
    "pi": {
        "permissions": SliceOutput(
            renderer="pi-project",
            output=".pi/extensions/pi-permission-system/config.json",
        ),
    },
}


def project_slices(harnesses: Iterable[str]) -> tuple[tuple[str, str, SliceOutput], ...]:
    """Every (agent, slice, output) these harnesses generate, in preset order."""
    return tuple(
        (harness, name, spec)
        for harness in harnesses
        for name, spec in PROJECT_PRESET[harness].items()
    )


def project_outputs(harnesses: Iterable[str]) -> tuple[str, ...]:
    """Every in-repo path these harnesses generate — what `.gitignore` needs."""
    return tuple(spec.output for _, _, spec in project_slices(harnesses) if spec.output is not None)
