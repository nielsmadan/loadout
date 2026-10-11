from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .output import ArtifactScope


@dataclass(frozen=True)
class InitOptions:
    root: Path
    scope: ArtifactScope
    source: Path | None = None
    agents: tuple[str, ...] = ()
    registration: str | None = None
    dry_run: bool = False
    json: bool = False
    yes: bool = False
