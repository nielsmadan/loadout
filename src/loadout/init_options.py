from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactScope
from .errors import UsageError
from .migration_models import RootMapping, SourceSelection


@dataclass(frozen=True)
class InitOptions:
    root: Path
    scope: ArtifactScope | None = None
    source: Path | None = None
    agents: tuple[str, ...] = ()
    mappings: tuple[RootMapping, ...] = ()
    selections: tuple[SourceSelection, ...] = ()
    registration: str | None = None
    dry_run: bool = False
    json: bool = False
    yes: bool = False
    starter: str | None = None
    git_hooks: str | None = None


def _object(value: str, required: set[str], optional: set[str]) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except ValueError as error:
        raise UsageError("mapping/selection must be a JSON object") from error
    if not isinstance(data, dict) or not required <= data.keys() <= required | optional:
        raise UsageError(f"expected JSON keys: {', '.join(sorted(required))}")
    return data


def _path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise UsageError("mapping paths must be nonempty strings")
    return Path(value).expanduser().absolute()


def parse_mapping(value: str) -> RootMapping:
    data = _object(
        value, {"source", "destination", "agents"}, {"kind", "category", "destination_template"}
    )
    agents = data["agents"]
    if not isinstance(agents, list) or not agents or not all(isinstance(a, str) for a in agents):
        raise UsageError("mapping agents must be a nonempty array of harness names")
    kind = data.get("kind", "harness")
    if not isinstance(kind, str) or kind not in {"harness", "shared", "file"}:
        raise UsageError("mapping kind must be harness, shared or file")
    for key in ("category", "destination_template"):
        if key in data and (not isinstance(data[key], str) or not data[key]):
            raise UsageError(f"mapping {key} must be a nonempty string")
    return RootMapping(
        _path(data["source"]),
        _path(data["destination"]),
        tuple(agents),
        "harness" if kind == "harness" else "shared" if kind == "shared" else "file",
        data.get("category"),
        data.get("destination_template"),
    )


def parse_selection(value: str) -> SourceSelection:
    data = _object(value, {"source", "destination"}, set())
    return SourceSelection(_path(data["destination"]), _path(data["source"]))
