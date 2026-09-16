from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError
from .harnesses import KNOWN_HARNESSES

MACHINE_CONFIG_NAME = "config.toml"


@dataclass(frozen=True)
class MachineConfig:
    """This machine's global source, active profile and project harness defaults."""

    source: Path
    profile: str | None = None
    harnesses: tuple[str, ...] = ()


def machine_state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where state that belongs to this machine rather than to a source lives.

    ADR 0010 named one place for it; ADR 0008 forbids the alternative of hiding it
    inside a generated file. Both the machine config and the record of what `sync`
    wrote resolve through here so they cannot drift apart.
    """
    environ = os.environ if env is None else env
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(environ.get("HOME", "~")).expanduser() / ".config"
    return base / "loadout"


def machine_config_path(env: Mapping[str, str] | None = None) -> Path:
    return machine_state_dir(env) / MACHINE_CONFIG_NAME


def load_machine_config(path: Path, *, require_source: bool = True) -> MachineConfig | None:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML: {error}") from error

    unknown = sorted(set(data) - {"source", "profile", "harnesses"})
    if unknown:
        raise LoadoutError(f"{path}: unknown key(s) {', '.join(unknown)}")

    raw_source = data.get("source")
    if not isinstance(raw_source, str) or not raw_source:
        raise LoadoutError(f"{path}: source must be a non-empty string naming a directory")

    source = Path(raw_source).expanduser()
    if require_source and not source.is_dir():
        raise LoadoutError(f"{path}: source {raw_source!r} is not a directory")

    profile = data.get("profile")
    if profile is not None and (not isinstance(profile, str) or not profile):
        raise LoadoutError(f"{path}: profile must be a non-empty string")

    raw_harnesses = data.get("harnesses")
    if raw_harnesses is None:
        harnesses: tuple[str, ...] = ()
    else:
        if (
            not isinstance(raw_harnesses, list)
            or not raw_harnesses
            or not all(isinstance(harness, str) for harness in raw_harnesses)
        ):
            raise LoadoutError(f"{path}: harnesses must be a non-empty list of strings")
        harnesses = tuple(raw_harnesses)
        if len(set(harnesses)) != len(harnesses):
            raise LoadoutError(f"{path}: harnesses contains duplicate entries")
        unknown_harnesses = sorted(set(harnesses) - KNOWN_HARNESSES)
        if unknown_harnesses:
            known = ", ".join(sorted(KNOWN_HARNESSES))
            raise LoadoutError(
                f"{path}: unknown harness(es) {', '.join(unknown_harnesses)} (known: {known})"
            )

    return MachineConfig(source=source.resolve(), profile=profile, harnesses=harnesses)
