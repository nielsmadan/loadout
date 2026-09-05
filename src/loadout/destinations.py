from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import LoadoutError

_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(template: str, label: str) -> str:
    """Substitute `${VAR}` and `${VAR:-fallback}`.

    Which variable a harness reads is recorded in docs/reference/, never here.
    An empty value counts as unset, matching `machine_config_path`; an empty
    fallback does too, so `${VAR:-}` cannot quietly resolve to nothing.
    """

    def substitute(match: re.Match[str]) -> str:
        name, fallback = match.group(1), match.group(2)
        value = os.environ.get(name)
        if value:
            return value
        if fallback:
            return fallback
        raise LoadoutError(
            f"{label}: destination {template!r} reads ${{{name}}}, which is unset or "
            f"empty, and has no fallback; give it one as ${{{name}:-<path>}}"
        )

    expanded = _ENV_REFERENCE.sub(substitute, template)
    # Anything brace-shaped left over is a reference this grammar does not cover
    # (`${VAR-x}`, `${VAR:?x}`, `${}`, an unclosed brace, a nested fallback). Left
    # alone it would become literal path text and be written to as a directory. A
    # stray `}` is the tell for the nested case, where the substitution succeeded
    # and only the inner reference's closing brace survives.
    if "${" in expanded or "}" in expanded:
        raise LoadoutError(
            f"{label}: destination {template!r} contains a reference loadout does not "
            f"understand; only ${{VAR}} and ${{VAR:-fallback}} are substituted"
        )
    return expanded


def resolve_destination(template: str, label: str) -> Path:
    """Resolve a destination template to the machine path `sync` will write.

    Deferred to render time rather than parse time for two reasons: a target the
    active profile does not select must not be able to fail the run over a variable
    this machine has no reason to set, and `~` resolves here too, so the checks
    below see the whole path rather than half of one."""
    expanded = _expand_env(template, label)
    try:
        path = Path(expanded).expanduser()
    except RuntimeError as error:
        raise LoadoutError(f"{label}: destination {template!r}: {error}") from error
    if not path.is_absolute() or ".." in path.parts:
        raise LoadoutError(
            f"{label}: destination {template!r} resolves to {str(path)!r}, which must be "
            f"an absolute path with no '..' components"
        )
    return path
