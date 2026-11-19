from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .composition import HEADER
from .harnesses import SliceOutput
from .json_paths import JsonPath

STEALTH_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "instructions": SliceOutput(output="CLAUDE.local.md"),
        "permissions": SliceOutput(output=".claude/settings.local.json"),
        "skills": SliceOutput(output=".claude/skills"),
    },
    "codex": {
        "instructions": SliceOutput(output=".codex/config.toml"),
        "mcp": SliceOutput(output=".codex/config.toml"),
        "permissions": SliceOutput(output=".codex/rules"),
        "skills": SliceOutput(output=".agents/skills"),
    },
    "cursor": {
        "instructions": SliceOutput(output=".cursor/rules"),
        "mcp": SliceOutput(output=".cursor/mcp.json"),
        "permissions": SliceOutput(output=".cursor/cli.json"),
        "skills": SliceOutput(output=".cursor/skills"),
    },
    "devin": {
        "instructions": SliceOutput(output=".devin/rules"),
        "mcp": SliceOutput(output=".devin/mcp_config.local.json"),
        "permissions": SliceOutput(output=".devin/config.local.json"),
        "skills": SliceOutput(output=".devin/skills"),
    },
    "droid": {
        "instructions": SliceOutput(output=".factory/AGENTS.md"),
        "mcp": SliceOutput(output=".factory/mcp.json"),
        "permissions": SliceOutput(output=".factory/settings.local.json"),
        "skills": SliceOutput(output=".factory/skills"),
    },
    "opencode": {
        "instructions": SliceOutput(output="opencode.json"),
        "mcp": SliceOutput(output="opencode.json"),
        "permissions": SliceOutput(output="opencode.json"),
        "skills": SliceOutput(output=".opencode/skills"),
    },
    "pi": {
        "instructions": SliceOutput(output=".pi/APPEND_SYSTEM.md"),
        "mcp": SliceOutput(output=".pi/mcp.json"),
        "permissions": SliceOutput(output=".pi/extensions/pi-permission-system/config.json"),
        "skills": SliceOutput(output=".pi/skills"),
    },
}

OPENCODE_INSTRUCTIONS = PurePosixPath(".loadout/opencode-instructions.md")

CLAUDE_RULE_LISTS = ("allow", "ask", "deny")
DROID_RULE_LISTS = ("commandAllowlist", "commandDenylist", "commandBlocklist")


@dataclass(frozen=True)
class TargetFacts:
    agents_md: bool
    claude_md_on_path: bool


NO_FACTS = TargetFacts(agents_md=False, claude_md_on_path=False)


def _user_memory_file(environ: Mapping[str, str]) -> Path:
    config_dir = environ.get("CLAUDE_CONFIG_DIR") or ""
    home = Path(environ["HOME"]) if environ.get("HOME") else Path.home()
    return Path(
        os.path.abspath((Path(config_dir) if config_dir else home / ".claude") / "CLAUDE.md")
    )


def gather_facts(target_root: Path, environ: Mapping[str, str] | None = None) -> TargetFacts:
    return target_facts(target_root, Path.is_file, environ)


def target_facts(
    target_root: Path, present: Callable[[Path], bool], environ: Mapping[str, str] | None = None
) -> TargetFacts:
    env = os.environ if environ is None else environ
    root = target_root.resolve()
    user_memory = _user_memory_file(env).resolve()
    candidates = (
        folder / name
        for folder in (root, *root.parents)
        for name in ("CLAUDE.md", ".claude/CLAUDE.md")
    )
    local_above = (folder / "CLAUDE.local.md" for folder in root.parents)
    found = any(present(path) and path.resolve() != user_memory for path in candidates)
    return TargetFacts(
        agents_md=present(root / "AGENTS.md"),
        claude_md_on_path=found or any(present(path) for path in local_above),
    )


def render_claude_local(instructions: str, *, import_agents: bool) -> str:
    parts = [HEADER, *(["@AGENTS.md"] if import_agents else []), instructions]
    return "\n\n".join(parts) + "\n"


def instruction_document(harness: str, fragments: Sequence[str], facts: TargetFacts) -> str:
    if harness != "claude":
        return "\n\n".join([HEADER, *fragments]) + "\n"
    return render_claude_local(
        "\n\n".join(fragments), import_agents=facts.agents_md and not facts.claude_md_on_path
    )


def _fragment_keys(
    fragment: Mapping[str, Any], reserved: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[JsonPath, Any], ...]:
    keys: list[tuple[JsonPath, Any]] = []
    for key, value in fragment.items():
        if key in reserved and isinstance(value, Mapping):
            keys.extend(
                ((key, inner), item) for inner, item in value.items() if inner not in reserved[key]
            )
        elif key not in reserved:
            keys.append(((key,), value))
    return tuple(keys)


def setting_keys(harness: str, fragment: Mapping[str, Any]) -> tuple[tuple[JsonPath, Any], ...]:
    if harness == "claude":
        return _fragment_keys(fragment, {"permissions": CLAUDE_RULE_LISTS})
    return tuple(
        (path, value)
        for path, value in _fragment_keys(fragment, {})
        if path[0] not in DROID_RULE_LISTS
    )
