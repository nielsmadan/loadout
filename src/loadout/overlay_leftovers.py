from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path

from .errors import LoadoutError

OVERLAYS_FILE = "overlays.toml"
BLOCK_PREFIX = "# >>> loadout overlay "
_REMOVE = (
    "uvx --python 3.13 --from git+https://github.com/nielsmadan/loadout.git@v0.10.0 "
    "loadout overlay remove {name} --yes"
)


def recovery_command(name: str = "NAME") -> str:
    return f"`{_REMOVE.format(name=name)}`"


FIRST_REMOVE = f"first remove any overlay 0.10.0 registered with {recovery_command()}"


class OverlayLeftover(LoadoutError):
    pass


class UnreadableOverlayRegistry(OverlayLeftover):
    pass


def _remove_named(names: Sequence[str]) -> str:
    if len(names) == 1:
        return f"remove it first with {recovery_command(names[0])}"
    return f"remove each first with {recovery_command()}"


def registry_leftover(path: Path, text: str) -> str | None:
    try:
        table = tomllib.loads(text).get("overlay", {})
    except tomllib.TOMLDecodeError as error:
        return unreadable_registry(path, str(error))
    if not isinstance(table, dict):
        return unreadable_registry(path, "`overlay` is not a table")
    if not table:
        return None
    names = sorted(table)
    noun = "overlay" if len(names) == 1 else "overlays"
    return f"{path} still registers the 0.10.0 {noun} {', '.join(names)}; {_remove_named(names)}"


def unreadable_registry(path: Path, detail: str) -> str:
    return (
        f"{path} is 0.10.0's overlay registry and cannot be read ({detail}); fix it and remove "
        f"each overlay with {recovery_command()}, or delete the file if no overlay is left"
    )


def overlay_blocks(text: str) -> tuple[str, ...]:
    names = (
        line.removeprefix(BLOCK_PREFIX).partition(":")[0].strip() or "NAME"
        for line in text.splitlines()
        if line.startswith(BLOCK_PREFIX)
    )
    return tuple(dict.fromkeys(names))


def exclude_leftover(path: Path, text: str) -> str | None:
    names = overlay_blocks(text)
    if not names:
        return None
    noun = "block" if len(names) == 1 else "blocks"
    return (
        f"{path} still holds the 0.10.0 overlay {noun} {', '.join(names)}; {_remove_named(names)}"
    )


def _read(path: Path) -> str | None:
    try:
        return path.read_bytes().decode("utf-8", "surrogateescape")
    except FileNotFoundError:
        return None


def check_registry(config_dir: Path) -> None:
    path = config_dir / OVERLAYS_FILE
    try:
        text = _read(path)
    except OSError as error:
        raise UnreadableOverlayRegistry(unreadable_registry(path, str(error.strerror))) from None
    problem = None if text is None else registry_leftover(path, text)
    if problem is not None:
        raise OverlayLeftover(problem)


def registry_notice(config_dir: Path) -> str | None:
    try:
        check_registry(config_dir)
    except OverlayLeftover as error:
        return str(error)
    return None


def check_exclude(path: Path) -> None:
    try:
        text = _read(path)
    except OSError as error:
        raise LoadoutError(f"cannot read {path}: {error.strerror}") from None
    problem = None if text is None else exclude_leftover(path, text)
    if problem is not None:
        raise OverlayLeftover(problem)
