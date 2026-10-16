from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError
from .harnesses import KNOWN_HARNESSES
from .manifest import MANIFEST_NAME

MACHINE_CONFIG_NAME = "config.toml"


@dataclass(frozen=True)
class MachineConfig:
    """This machine's global source, active profile and project harness defaults."""

    source: Path
    profile: str | None = None
    harnesses: tuple[str, ...] = ()


def config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where state that belongs to this machine rather than to a source lives.

    ADR 0010 named one place for it; ADR 0008 forbids the alternative of hiding it
    inside a generated file. Both the machine config and the record of what `sync`
    wrote resolve through here so they cannot drift apart.
    """
    environ = os.environ if env is None else env
    xdg = environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(environ.get("HOME", "~")).expanduser() / ".config"
    return base / "loadout"


def state_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where what loadout keeps for itself lives: init's backups, sync's full diff."""
    environ = os.environ if env is None else env
    state = environ.get("XDG_STATE_HOME")
    base = Path(state) if state else Path(environ.get("HOME", "~")).expanduser() / ".local/state"
    return (base / "loadout").resolve()


def machine_config_path(env: Mapping[str, str] | None = None) -> Path:
    return config_dir(env) / MACHINE_CONFIG_NAME


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


def resolve_profile(root: Path, explicit: str | None = None, *, lenient: bool = False) -> str:
    """The profile a run at `root` renders: an explicit one, else the machine's when
    `root` is the registered global source, else `default`. One rule for sync, check,
    the git hooks and init, so no two of them retire each other's outputs (ADR 0022).

    An unreadable machine config is an error at a global source, whose profile it may
    name; falling back there renders `default` and retires the profile's outputs. A
    project, or a hook (`lenient`), falls back to `default`, where that is harmless."""
    if explicit:
        return explicit
    try:
        config = load_machine_config(machine_config_path(), require_source=False)
    except LoadoutError:
        if (root / MANIFEST_NAME).is_file() and not lenient:
            raise
        return "default"
    return profile_for(root, None, config)


def profile_for(root: Path, explicit: str | None, config: MachineConfig | None) -> str:
    """`resolve_profile`'s rule, given the machine config already loaded."""
    if explicit:
        return explicit
    if config is not None and is_registered(root, config.source):
        return config.profile or "default"
    return "default"


def is_registered(root: Path, source: Path | None) -> bool:
    """Whether `root` is the registered source, through a link or a relative spelling."""
    return source is not None and source.resolve() == root.resolve()


def registered_source() -> Path | None:
    """This machine's registered global source; None when there is no readable machine
    config, so a missing or broken one registers nothing (ADR 0031)."""
    try:
        config = load_machine_config(machine_config_path(), require_source=False)
    except LoadoutError:
        return None
    return None if config is None else config.source
