from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LoadoutError

PROJECT_DIR = "loadout"
PROJECT_CONFIG_NAME = "config.toml"

KNOWN_HARNESSES = frozenset({"claude", "codex", "opencode", "pi"})


@dataclass(frozen=True)
class ProjectConfig:
    """Which harnesses this project generates configuration for.

    Validation lives here, not in load_project_config, so it cannot be bypassed
    by constructing a ProjectConfig directly (as init_project used to) — see the
    milestone 4 fix-wave note on the duplicate-harness defect this closed.
    """

    harnesses: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.harnesses:
            raise LoadoutError("at least one harness is required")
        if len(set(self.harnesses)) != len(self.harnesses):
            raise LoadoutError("duplicate harness in the list")
        bad = sorted(set(self.harnesses) - KNOWN_HARNESSES)
        if bad:
            known = ", ".join(sorted(KNOWN_HARNESSES))
            raise LoadoutError(f"unknown harness(es) {', '.join(bad)} (known: {known})")


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

    unknown = sorted(set(data) - {"harnesses"})
    if unknown:
        raise LoadoutError(
            f"{path}: unrecognised key(s) {', '.join(unknown)}; "
            f"'harnesses' is the only key this file accepts"
        )

    raw = data.get("harnesses")
    if not isinstance(raw, list) or not all(isinstance(h, str) for h in raw):
        raise LoadoutError(f"{path}: harnesses must be a list of strings")

    try:
        return ProjectConfig(harnesses=tuple(raw))
    except LoadoutError as error:
        raise LoadoutError(f"{path}: {error}") from error


@dataclass(frozen=True)
class ProjectTarget:
    path: PurePosixPath
    renderer: str
    preserve_foreign: bool = False


PRESET: dict[str, tuple[ProjectTarget, ...]] = {
    "claude": (
        ProjectTarget(
            PurePosixPath(".claude/settings.json"), "claude-project", preserve_foreign=True
        ),
        ProjectTarget(PurePosixPath(".claude/mcp-permissions.json"), "claude-mcp"),
    ),
    "codex": (ProjectTarget(PurePosixPath(".codex/rules/permissions.rules"), "codex-project"),),
    "opencode": (ProjectTarget(PurePosixPath("opencode.json"), "opencode", preserve_foreign=True),),
    "pi": (
        ProjectTarget(
            PurePosixPath(".pi/extensions/pi-permission-system/config.json"),
            "pi-project",
        ),
    ),
}


def project_targets(config: ProjectConfig) -> tuple[ProjectTarget, ...]:
    targets: list[ProjectTarget] = []
    for harness in config.harnesses:
        targets.extend(PRESET[harness])
    return tuple(targets)
