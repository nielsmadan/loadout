from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError

MACHINE_CONFIG_NAME = "config.toml"


@dataclass(frozen=True)
class MachineConfig:
    """Where this machine's global source lives, and which profile it runs."""

    source: Path
    profile: str | None = None


def machine_config_path(env: Mapping[str, str] | None = None) -> Path:
    environ = os.environ if env is None else env
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(environ.get("HOME", "~")).expanduser() / ".config"
    return base / "loadout" / MACHINE_CONFIG_NAME


def load_machine_config(path: Path) -> MachineConfig | None:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error

    unknown = sorted(set(data) - {"source", "profile"})
    if unknown:
        raise LoadoutError(f"{path}: unknown key(s) {', '.join(unknown)}")

    raw_source = data.get("source")
    if not isinstance(raw_source, str) or not raw_source:
        raise LoadoutError(f"{path}: source must be a non-empty string naming a directory")

    source = Path(raw_source).expanduser()
    if not source.is_dir():
        raise LoadoutError(f"{path}: source {raw_source!r} is not a directory")

    profile = data.get("profile")
    if profile is not None and (not isinstance(profile, str) or not profile):
        raise LoadoutError(f"{path}: profile must be a non-empty string")

    return MachineConfig(source=source.resolve(), profile=profile)
