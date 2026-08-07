from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import LoadoutError

PROJECT_DIR = "loadout"
PROJECT_CONFIG_NAME = "config.toml"

KNOWN_HARNESSES = frozenset({"claude", "codex", "opencode", "pi", "antigravity"})


@dataclass(frozen=True)
class ProjectConfig:
    """Which harnesses this project generates configuration for."""

    harnesses: tuple[str, ...]


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
    if not raw:
        raise LoadoutError(f"{path}: at least one harness is required")
    if len(set(raw)) != len(raw):
        raise LoadoutError(f"{path}: duplicate harness in the list")

    bad = sorted(set(raw) - KNOWN_HARNESSES)
    if bad:
        known = ", ".join(sorted(KNOWN_HARNESSES))
        raise LoadoutError(f"{path}: unknown harness(es) {', '.join(bad)} (known: {known})")

    return ProjectConfig(harnesses=tuple(raw))


@dataclass(frozen=True)
class ProjectTarget:
    path: PurePosixPath
    renderer: str


PRESET: dict[str, tuple[ProjectTarget, ...]] = {
    "claude": (
        ProjectTarget(PurePosixPath(".claude/settings.json"), "claude-project"),
        ProjectTarget(PurePosixPath(".aiconf/mcp-permissions.json"), "claude-mcp"),
    ),
    "codex": (ProjectTarget(PurePosixPath(".codex/rules/aiconf.rules"), "codex-project"),),
    "opencode": (ProjectTarget(PurePosixPath("opencode.json"), "opencode"),),
    "pi": (
        ProjectTarget(
            PurePosixPath(".pi/extensions/pi-permission-system/config.json"),
            "pi-project",
        ),
    ),
    "antigravity": (),
}


def project_targets(config: ProjectConfig) -> tuple[ProjectTarget, ...]:
    targets: list[ProjectTarget] = []
    for harness in config.harnesses:
        targets.extend(PRESET[harness])
    return tuple(targets)
