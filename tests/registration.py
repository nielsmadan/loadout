"""Registers a root as this machine's global source, which global scope needs before it
renders (ADR 0031). Each test that renders global scope calls it itself, never through an
autouse fixture, so a test of the refusal cannot be masked by it."""

from __future__ import annotations

import json
from pathlib import Path

from loadout.machine import machine_config_path


def register(source: Path, profile: str | None = None) -> Path:
    path = machine_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"source = {json.dumps(str(source))}"]
    if profile is not None:
        lines.append(f"profile = {json.dumps(profile)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return source
