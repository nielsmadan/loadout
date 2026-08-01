from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .composition import render
from .targets import TARGETS


def fragments_dir(root: Path) -> Path:
    return root / "global" / "fragments"


def render_all(root: Path) -> dict[Path, str]:
    fragments = fragments_dir(root)
    return {root / str(t.path): render(t, fragments) for t in TARGETS}


def atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".loadout-")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_all(root: Path) -> list[Path]:
    written: list[Path] = []
    for path, content in render_all(root).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, content)
        written.append(path)
    return written


def check_all(root: Path) -> list[tuple[Path, str, str]]:
    drift: list[tuple[Path, str, str]] = []
    for path, expected in render_all(root).items():
        actual = path.read_text() if path.is_file() else ""
        if actual != expected:
            drift.append((path, actual, expected))
    return drift
