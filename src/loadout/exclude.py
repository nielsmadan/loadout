from __future__ import annotations

import json
import stat
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import machine
from .atomic import TEMPORARY_PREFIX, replace_atomically
from .containment import no_symlinks
from .errors import LoadoutError
from .gitignore import escape
from .overlay_git import covered_by, identity
from .overlay_lock import file_lock

BLOCK_BEGIN = (
    "# >>> loadout overlay {name}: managed by loadout; lines inside this block are replaced"
)
BLOCK_END = "# <<< loadout overlay {name}"
GLOBAL_BEGIN = "# >>> loadout overlays: personal files, managed by loadout"
GLOBAL_END = "# <<< loadout overlays"
PERSONAL_UNITS: tuple[str, ...] = (
    "CLAUDE.local.md",
    ".claude/settings.local.json",
    ".factory/settings.local.json",
)
TEMP_PATTERN = f"{TEMPORARY_PREFIX}*"

_ENCODING = "utf-8"
_ERRORS = "surrogateescape"
_DEFAULT_MODE = 0o644


def _split(text: str) -> list[str]:
    parts = text.split("\n")
    lines = [f"{part}\n" for part in parts[:-1]]
    return [*lines, parts[-1]] if parts[-1] else lines


def _bare(line: str) -> str:
    return line.removesuffix("\n").removesuffix("\r")


def _span(lines: list[str], begin: str, end: str) -> tuple[int, int] | None:
    starts = [i for i, line in enumerate(lines) if _bare(line) == begin]
    if not starts:
        return None
    if len(starts) > 1:
        raise LoadoutError(f"the block {begin!r} appears more than once; remove the extra copies")
    stops = [i for i in range(starts[0] + 1, len(lines)) if _bare(lines[i]) == end]
    if not stops:
        raise LoadoutError(f"the block {begin!r} has no closing line {end!r}")
    return starts[0], stops[0]


def block_lines(text: str, begin: str, end: str) -> tuple[str, ...] | None:
    lines = _split(text)
    found = _span(lines, begin, end)
    if found is None:
        return None
    return tuple(_bare(line) for line in lines[found[0] + 1 : found[1]])


def _blank(line: str) -> bool:
    return _bare(line) == ""


def with_block(text: str, begin: str, end: str, lines: Sequence[str]) -> str:
    existing = _split(text)
    found = _span(existing, begin, end)
    block = [f"{line}\n" for line in (begin, *lines, end)]
    if found is None:
        if not lines:
            return text
        separator = [] if not existing or _blank(existing[-1]) else ["\n"]
        if existing and not existing[-1].endswith("\n"):
            existing[-1] += "\n"
        return "".join([*existing, *separator, *block])
    start, stop = found
    if lines:
        return "".join([*existing[:start], *block, *existing[stop + 1 :]])
    closes_a_section = stop == len(existing) - 1 or _blank(existing[stop + 1])
    if closes_a_section and start > 0 and _blank(existing[start - 1]):
        start -= 1
    return "".join([*existing[:start], *existing[stop + 1 :]])


def anchored(unit: str, *, folder: bool) -> str:
    return f"/{escape(unit)}{'/' if folder else ''}"


def global_pattern(unit: str) -> str:
    return f"**/{escape(unit)}"


def exclude_path(common_dir: Path) -> Path:
    return common_dir / "info" / "exclude"


def _read_text(path: Path) -> tuple[str, int]:
    try:
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise LoadoutError(f"{path} must be a regular file")
        raw = path.read_bytes()
    except FileNotFoundError:
        return "", _DEFAULT_MODE
    return raw.decode(_ENCODING, _ERRORS), stat.S_IMODE(metadata.st_mode)


def _transformed(path: Path, transform: Callable[[str], str], text: str) -> str:
    try:
        return transform(text)
    except LoadoutError as error:
        raise LoadoutError(f"{path}: {error}") from None


def _edit(
    path: Path, transform: Callable[[str], str], boundary: Path | None, *, parents: bool
) -> bool:
    try:
        with file_lock(path):
            text, mode = _read_text(path)
            updated = _transformed(path, transform, text)
            if updated == text:
                return False
            path.parent.mkdir(parents=parents, exist_ok=True)
            no_symlinks(path, boundary)
            replace_atomically(
                path,
                lambda handle: handle.write(updated.encode(_ENCODING, _ERRORS)),
                mode=mode,
                durable=True,
                before_rename=lambda: no_symlinks(path, boundary),
            )
            return True
    except OSError as error:
        raise LoadoutError(f"cannot edit {path}: {error}") from None


def write_repo_block(common_dir: Path, name: str, patterns: Sequence[str]) -> bool:
    if not common_dir.is_dir():
        raise LoadoutError(f"the git directory {common_dir} does not exist")
    path = exclude_path(common_dir)
    no_symlinks(path.parent, common_dir)
    no_symlinks(path, common_dir)
    own = tuple(pattern for pattern in dict.fromkeys(patterns) if pattern != TEMP_PATTERN)
    wanted = (*own, TEMP_PATTERN) if own else ()
    begin, end = BLOCK_BEGIN.format(name=name), BLOCK_END.format(name=name)
    return _edit(path, lambda text: with_block(text, begin, end, wanted), common_dir, parents=False)


@dataclass(frozen=True)
class GlobalIgnoreState:
    answer: Literal["unasked", "accepted", "declined"]
    file: Path | None


def global_state_path() -> Path:
    return machine.config_dir() / "overlay-global-ignore.json"


def read_global_state() -> GlobalIgnoreState:
    path = global_state_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return GlobalIgnoreState("unasked", None)
    except (OSError, ValueError) as error:
        raise LoadoutError(f"cannot read {path}: {error}; fix or delete it") from None
    answer = raw.get("answer") if isinstance(raw, dict) else None
    file = raw.get("file") if isinstance(raw, dict) else None
    if answer not in ("unasked", "accepted", "declined") or not (
        file is None or isinstance(file, str)
    ):
        raise LoadoutError(f"cannot read {path}: unexpected contents; fix or delete it")
    return GlobalIgnoreState(answer, Path(file) if file is not None else None)


def write_global_state(state: GlobalIgnoreState) -> None:
    path = global_state_path()
    text = (
        json.dumps(
            {"answer": state.answer, "file": str(state.file) if state.file else None}, indent=2
        )
        + "\n"
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        replace_atomically(path, lambda handle: handle.write(text.encode("utf-8")), durable=True)
    except OSError as error:
        raise LoadoutError(f"cannot write {path}: {error}") from None


def user_covered(scratch: Path, file: Path, units: Iterable[str]) -> frozenset[str]:
    text, _ = _read_text(file.resolve())
    own = _transformed(
        file, lambda current: with_block(current, GLOBAL_BEGIN, GLOBAL_END, ()), text
    )
    with tempfile.TemporaryDirectory(prefix="loadout-overlay-") as folder:
        copy = Path(folder) / "ignore"
        copy.write_bytes(own.encode(_ENCODING, _ERRORS))
        return covered_by(scratch, copy, units, frozenset())


def write_global_block(file: Path, patterns: Callable[[Sequence[str]], Sequence[str]]) -> bool:
    def transform(text: str) -> str:
        outside = with_block(text, GLOBAL_BEGIN, GLOBAL_END, ())
        wanted = tuple(dict.fromkeys(patterns([_bare(line) for line in _split(outside)])))
        return with_block(text, GLOBAL_BEGIN, GLOBAL_END, wanted)

    return _edit(file.resolve(), transform, None, parents=True)


def _existing_folder(path: Path) -> Path:
    probe = path.parent
    while not probe.is_dir():
        probe = probe.parent
    return probe


def offer_text(units: Sequence[str], file: Path) -> str:
    resolved = file.resolve()
    found = identity(_existing_folder(resolved))
    inside = (
        f"; it is in the git repository at {found.toplevel}; the block will show there as a change"
        if found is not None
        else ""
    )
    return (
        f"Ignore {', '.join(units)} in every repository? loadout would add a marked block to "
        f"{resolved}{inside}. Other overlay paths stay ignored per repository."
    )
