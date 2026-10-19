"""A plain copy of the bundled skill into each harness's user skills folder, so the skill
can do a conversion init could not (ADR 0033). Nothing records it: no source, no
registration, no write record."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bundled_skill import SKILL_NAME, bundled_skill_path
from .destinations import resolve_destination
from .errors import LoadoutError
from .harnesses import GLOBAL_PRESET, SHARED_SKILL_READERS
from .templates import tree_hash

# Codex, OpenCode and Pi all read `~/.agents/skills` (docs/reference/config.md, Skills).
SHARED_SKILLS = "~/.agents/skills"

STARTS = {
    "claude": "type /loadout",
    "codex": "type $loadout",
    "droid": "type /loadout",
    "opencode": "ask it to use the loadout skill",
    "pi": "type /skill:loadout",
}


def skill_folders(
    harnesses: Iterable[str], environ: Mapping[str, str] | None = None
) -> dict[Path, tuple[str, ...]]:
    """Each user-level skills folder the harnesses read, with the harnesses reading it."""
    folders: dict[Path, tuple[str, ...]] = {}
    for harness in dict.fromkeys(harnesses):
        template = (
            SHARED_SKILLS
            if harness in SHARED_SKILL_READERS
            else GLOBAL_PRESET[harness]["skills"].destination
        )
        assert template is not None
        folder = resolve_destination(template, harness, environ)
        folders[folder] = (*folders.get(folder, ()), harness)
    return folders


def copy_command(harnesses: Iterable[str]) -> str:
    flags = " ".join(f"--harness {harness}" for harness in dict.fromkeys(harnesses))
    return f"loadout integrate skill copy {flags}".rstrip()


def copy_skill(harnesses: Iterable[str], bundle: Path | None = None) -> tuple[Path, ...]:
    """Copy the skill into each folder that lacks it; the copies made. An existing copy
    that differs is refused before anything is copied, never overwritten."""
    source = bundled_skill_path() if bundle is None else bundle
    targets = [folder / SKILL_NAME for folder in skill_folders(harnesses)]
    expected = tree_hash(source)
    present = [t for t in targets if t.exists() or t.is_symlink()]
    differing = [t for t in present if t.is_symlink() or not t.is_dir() or tree_hash(t) != expected]
    if differing:
        raise LoadoutError(
            "a different loadout skill is already at "
            + ", ".join(str(t) for t in differing)
            + "; nothing was copied. Remove it, or use the copy that is there"
        )
    copied = []
    for target in targets:
        if target in present:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Copied beside the target and renamed into place, so a kill leaves no half skill.
        staging = Path(tempfile.mkdtemp(dir=target.parent, prefix=".loadout-skill-"))
        try:
            shutil.copytree(
                source, staging / SKILL_NAME, ignore=shutil.ignore_patterns("__pycache__")
            )
            (staging / SKILL_NAME).rename(target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        copied.append(target)
    return tuple(copied)


def start_hints(harnesses: Iterable[str]) -> tuple[str, ...]:
    return tuple(f"In {harness}, {STARTS[harness]}." for harness in dict.fromkeys(harnesses))


def cmd_skill_copy(harnesses: tuple[str, ...]) -> int:
    folders = skill_folders(harnesses)
    copied = copy_skill(harnesses)
    for folder in folders:
        target = folder / SKILL_NAME
        print(f"{'Copied the loadout skill to' if target in copied else 'Already there:'} {target}")
    for hint in start_hints(harnesses):
        print(hint)
    return 0
