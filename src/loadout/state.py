"""One root as a command finds it, below every module that reads it (review 5, batch 3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .project import project_config_path
from .written import WrittenEntry, read_written


@dataclass(frozen=True)
class SyncState:
    """One root as a command finds it: whether it holds a project, and what sync last
    wrote there. Read once and passed down, rather than probed again by every step
    (review 3, batch 4.4)."""

    root: Path
    project: bool
    written: Mapping[Path, WrittenEntry]


def sync_state(root: Path) -> SyncState:
    return SyncState(root, project_config_path(root).is_file(), read_written(root))
