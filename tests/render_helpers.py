from __future__ import annotations

from pathlib import Path

from loadout import emit, files


def write_all(root: Path, profile: str = "default") -> list[Path]:
    # Through the module, so a test that patches emit.render_all is honoured.
    return files.write_outputs(emit.render_all(root.absolute(), profile))
