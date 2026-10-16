from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .output import Scope


@dataclass(frozen=True)
class InitOptions:
    root: Path
    scope: Scope
    harnesses: tuple[str, ...] = ()
    registration: Literal["keep", "replace"] | None = None
    dry_run: bool = False
    json: bool = False
    yes: bool = False
