from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from .discovery import detected_global_harnesses
from .errors import LoadoutError, UsageError
from .harnesses import KNOWN_HARNESSES
from .machine import load_machine_config, machine_config_path
from .manifest import MANIFEST_NAME
from .stealth_commands import (
    interactive,
    refuse_overlay_registry,
    refuse_registration,
    register_stealth,
    stealth_name,
)
from .stealth_manifest import load_stealth_manifest, parse_targets
from .stealth_sync import expansion_notes, shown
from .stealth_targets import (
    Expansion,
    HarnessHomes,
    expand_targets,
    harness_homes,
    refuse_patterns_inside,
    stealth_folder_reason,
    unsafe_stealth_folder,
)

GUIDE = "https://loadoutai.dev/guides/stealth/#write-a-stealth-source"
TARGETS_EXAMPLE = 'loadout stealth init --targets "~/work/app*"'
_YES = frozenset({"y", "yes"})


def spelled_target(pattern: str) -> str:
    if not pattern.startswith("/"):
        return pattern
    spelled = shown(Path(pattern))
    return spelled if spelled.startswith("~/") else pattern


def manifest_text(targets: Sequence[str], name: str, harnesses: Sequence[str]) -> str:
    listed = ", ".join(json.dumps(target, ensure_ascii=False) for target in targets)
    tables = "".join(f"[{harness}]\n" for harness in harnesses)
    return f'[stealth]\ntargets = [{listed}]\n\n[[source]]\nname = "{name}"\npath = "."\n\n{tables}'


def resolve_harnesses(
    flagged: Sequence[str], machine: Sequence[str], detected: Sequence[str]
) -> tuple[str, ...]:
    chosen = set(flagged or machine or detected)
    return tuple(harness for harness in sorted(KNOWN_HARNESSES) if harness in chosen)


def _machine_harnesses() -> tuple[str, ...]:
    config = load_machine_config(machine_config_path(), require_source=False)
    return config.harnesses if config is not None else ()


def _harnesses(flagged: Sequence[str], home: Path, environ: Mapping[str, str]) -> tuple[str, ...]:
    if flagged:
        return resolve_harnesses(flagged, (), ())
    harnesses = resolve_harnesses(
        (), _machine_harnesses(), detected_global_harnesses(home, environ)
    )
    if not harnesses:
        raise UsageError(
            "no harness is configured on this machine or detected; pass --harness NAME, "
            "once for each"
        )
    return harnesses


def _refuse_folder(folder: Path, homes: HarnessHomes) -> None:
    if (reason := unsafe_stealth_folder(folder, homes)) is not None:
        raise LoadoutError(f"{shown(folder)}: {reason}")


def _refuse_matches(folder: Path, expansion: Expansion) -> None:
    for skip in expansion.skipped:
        if skip.kind == "unsafe" and (reason := stealth_folder_reason(skip.folder, folder)):
            raise LoadoutError(f"{shown(skip.folder)}: {reason}")


def _report(name: str, expansion: Expansion) -> None:
    lines = (
        *(shown(target.root) for target in expansion.targets),
        *(f"{shown(skip.folder)}: skipped: {skip.reason}" for skip in expansion.skipped),
        *expansion_notes(expansion),
    )
    print(f"stealth {name}: targets matching now:")
    for line in lines or ("none yet; a clone that matches is picked up by the next sync",):
        print(f"  {line}")


def _next_steps(name: str, folder: Path) -> None:
    where = shown(folder)
    print(
        f"next: add mcp.toml, permissions.toml or skills/<skill>/ to {where}; for instructions, "
        'add instructions/<name>.md and list it under [all] as instructions = ["<name>"]'
    )
    print(f"layout: {GUIDE}")
    print(f"then: loadout sync --stealth {name}")


def _confirmed(name: str, folder: Path, yes: bool) -> bool:
    if yes:
        return True
    try:
        answer = input(
            f"create {folder / MANIFEST_NAME} and register stealth source {name}, which writes "
            "into every checkout its targets match? [y/N] "
        )
    except (EOFError, OSError) as error:
        raise UsageError("stealth init needs --yes when input is not interactive") from error
    return answer.strip().lower() in _YES


def missing_folders(folder: Path) -> tuple[Path, ...]:
    return tuple(ancestor for ancestor in (folder, *folder.parents) if not ancestor.exists())


def _roll_back(path: Path, created: tuple[Path, ...]) -> None:
    path.unlink(missing_ok=True)
    for made in created:
        try:
            made.rmdir()
        except OSError:
            return


def _write(folder: Path, text: str, name: str) -> None:
    created = missing_folders(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MANIFEST_NAME
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(text)
        load_stealth_manifest(folder)
        register_stealth(folder, name)
    except BaseException:
        _roll_back(path, created)
        raise


def cmd_stealth_init(
    directory: Path | None,
    targets: Sequence[str] | None,
    *,
    name: str | None,
    harnesses: Sequence[str] | None,
    yes: bool,
) -> int:
    if not targets:
        raise UsageError(
            f"stealth init needs --targets, once for each pattern; for example: {TARGETS_EXAMPLE}"
        )
    refuse_overlay_registry()
    folder = (directory or Path.cwd()).expanduser().resolve()
    path = folder / MANIFEST_NAME
    if os.path.lexists(path):
        raise LoadoutError(
            f"{path} already exists, and stealth init never overwrites one; if it holds a "
            f"[stealth] table, register it with `loadout stealth add {shown(folder)}`"
        )
    if folder.exists() and not folder.is_dir():
        raise LoadoutError(f"{folder} is not a folder")
    chosen = stealth_name(folder, name)
    spelled = tuple(dict.fromkeys(spelled_target(pattern) for pattern in targets))
    settings = parse_targets(list(spelled), path)
    homes = harness_homes()
    resolved = _harnesses(harnesses or (), homes.home, os.environ)
    _refuse_folder(folder, homes)
    refuse_patterns_inside(settings, homes.home, folder)
    expansion = expand_targets(settings, homes, folder)
    _refuse_matches(folder, expansion)
    refuse_registration(folder, chosen)
    if not yes and not interactive():
        raise UsageError("stealth init needs --yes when input is not interactive")
    _report(chosen, expansion)
    if not _confirmed(chosen, folder, yes):
        print("declined; no changes made")
        return 0
    _write(folder, manifest_text(spelled, chosen, resolved), chosen)
    _next_steps(chosen, folder)
    return 0
