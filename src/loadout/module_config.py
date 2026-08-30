"""The module-config slice — a module's own configuration, carried verbatim.

A harness fixes the *directory* its modules read config from and leaves the
filename to each module, so there is nothing to derive and nothing to render.
Pi is the worked case: `ExtensionAPI` (0.84.1) exposes no config accessor, and
its docs tell an extension to compose its own path. Two shapes result —
`<agent-dir>/pi-statusline.json` and `<agent-dir>/extensions/subagent/config.json`
— and the second is why the path is authored rather than derived: the directory
is `subagent` while the package is `pi-subagents`.

So the relative path *is* the declaration, exactly as a directory is for skills,
and the bytes are copied rather than serialised: a module writes its own file
with its own formatter, and re-emitting it through `json.dumps` would rewrite
every line of a file loadout has no schema for.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from .skills import EXCLUDED_DIRECTORIES, EXCLUDED_NAMES, EXCLUDED_SUFFIXES

MODULE_CONFIG_SUBDIR = "module-config"


def _excluded(path: Path, relative: PurePosixPath) -> bool:
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    return path.name in EXCLUDED_NAMES or path.suffix in EXCLUDED_SUFFIXES


def discover_module_config(agent_root: Path) -> tuple[PurePosixPath, ...]:
    """Every file under one agent's tree, as sorted relative paths.

    An absent tree is not an error: a source that offers module config for one
    agent says nothing about the others.
    """
    if not agent_root.is_dir():
        return ()
    found: list[PurePosixPath] = []
    for item in sorted(agent_root.rglob("*")):
        if not item.is_file():
            continue
        relative = PurePosixPath(item.relative_to(agent_root).as_posix())
        if _excluded(item, relative):
            continue
        found.append(relative)
    return tuple(found)
