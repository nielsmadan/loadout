from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import tomlkit

from . import stealth_seams
from .commands import KitMode, choose_kit, skill_bytes
from .composition import fragment_paths
from .containment import no_symlinks
from .emit import ProjectKits, kit_skills, skill_trees
from .errors import LoadoutError, UsageError
from .gitignore import replace_text
from .kit_catalog import load_catalogs
from .kit_discover import available_kits
from .manifest import MANIFEST_NAME
from .stealth_commands import refuse_overlay_registry, registered_by_name
from .stealth_kits import LinkedKit, link_each, link_kits
from .stealth_manifest import (
    KITS_KEY,
    STEALTH_TABLE,
    StealthManifest,
    linked_kit_names,
    stealth_manifest_from,
)
from .stealth_registry import RegisteredStealth
from .stealth_sync import shown

VENDORED_REFUSAL = (
    "a stealth source links its kits, so every sync picks up the kit's current contents; "
    "--vendored copies a kit into a project. Drop --vendored"
)


def _registered(stealth: str) -> tuple[RegisteredStealth, Path]:
    refuse_overlay_registry()
    registered = registered_by_name(stealth)
    if registered.removing:
        raise LoadoutError(
            f"stealth source {stealth} is being removed; run `loadout stealth remove {stealth}`"
        )
    path = registered.folder / MANIFEST_NAME
    no_symlinks(path, registered.folder)
    return registered, path


def _with_kit(text: str, name: str) -> str:
    document = tomlkit.parse(text)
    table = document[STEALTH_TABLE]
    assert isinstance(table, dict)
    current = table.get(KITS_KEY)
    if current is None:
        table[KITS_KEY] = [name]
    else:
        current.append(name)
    rendered = tomlkit.dumps(document)
    return re.sub(r"(?<!\r)\n", "\r\n", rendered) if "\r\n" in text else rendered


def _choose(stealth: str, path: Path, linked: tuple[str, ...]) -> str:
    if not sys.stdin.isatty():
        raise UsageError(
            "`kit add` needs a name when not attached to a terminal: "
            f"`loadout kit add <name> --stealth {stealth}`"
        )
    offered = tuple(option for option in available_kits(None) if option.name not in linked)
    linkable = {
        name
        for name, result in link_each(tuple(option.name for option in offered), path)
        if isinstance(result, LinkedKit)
    }
    options = tuple(option for option in offered if option.name in linkable)
    if not options:
        raise UsageError(
            f"no kit left to link into {stealth}: every kit your sources offer is linked "
            f"already or cannot be linked; `loadout kit list --stealth {stealth}` shows why"
        )
    return choose_kit(options)


def _candidate(registered: RegisteredStealth, path: Path, text: str) -> StealthManifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"{path}: invalid TOML after adding the kit: {error}") from None
    candidate = stealth_manifest_from(registered.folder, data)
    link_kits(candidate.kit_names, path)
    return candidate


def _shadowing(manifest: StealthManifest, kit: LinkedKit) -> tuple[str, ...]:
    kits = ProjectKits((kit.path,), load_catalogs((kit.path,)))
    offered = kit_skills(kits)
    skills = tuple(
        f"{shown(skill.supporting_root)} is byte for byte the skill {kit.name} supplies; while "
        "it is there it replaces the kit's and stops following it, so delete it to take the "
        f"kit's: rm -r {shown(skill.supporting_root)}"
        for skill in skill_trees(manifest.manifest)
        if skill.name in offered and skill_bytes(skill) == skill_bytes(offered[skill.name])
    )
    parts = {path.read_bytes() for path in kits.contributions("instructions")}
    own = dict.fromkeys(
        path
        for target in manifest.manifest.targets
        for path in fragment_paths(target, manifest.manifest)
    )
    fragments = tuple(
        f"{shown(path)} is byte for byte an instruction part {kit.name} supplies, so both "
        f"copies are rendered; remove {path.stem} from the instructions lists and delete the file"
        for path in own
        if path.read_bytes() in parts
    )
    return (*skills, *fragments)


def cmd_stealth_kit_add(stealth: str, name: str | None, mode: KitMode | None) -> int:
    if mode == "vendored":
        raise UsageError(VENDORED_REFUSAL)
    registered, path = _registered(stealth)
    linked = linked_kit_names(stealth_seams.read_toml(path), path)
    chosen = name if name is not None else _choose(stealth, path, linked)
    if chosen in linked:
        print(f"{chosen} is already linked in {shown(path)}")
        return 0
    ((_, found),) = link_each((chosen,), path)
    if isinstance(found, LoadoutError):
        raise found
    spelled = next(
        (
            other
            for other, result in link_each(linked, path)
            if isinstance(result, LinkedKit) and result.path == found.path
        ),
        None,
    )
    if spelled is not None:
        print(f"{chosen} is already linked in {shown(path)} as {spelled}")
        return 0
    after = _with_kit(path.read_bytes().decode("utf-8"), chosen)
    candidate = _candidate(registered, path, after)
    replace_text(path, after, registered.folder)
    print(f"linked {chosen} in {shown(path)}")
    print(f"resolves from source {found.source}: {shown(found.path)}")
    for note in _shadowing(candidate, found):
        print(f"note: {note}")
    print(
        f"preview with `loadout check --stealth {stealth}`, then `loadout sync --stealth {stealth}`"
    )
    return 0


def cmd_stealth_kit_list(stealth: str) -> int:
    _, path = _registered(stealth)
    linked = linked_kit_names(stealth_seams.read_toml(path), path)
    if not linked:
        print(
            f"no kits in stealth source {stealth} — add one with "
            f"`loadout kit add <name> --stealth {stealth}`"
        )
        return 0
    for name, result in link_each(linked, path):
        if isinstance(result, LoadoutError):
            print(f"{name}: unresolved")
            print(f"    {result}")
            continue
        print(f"{name}: linked, from source {result.source}")
        print(f"    {shown(result.path)}")
    return 0
