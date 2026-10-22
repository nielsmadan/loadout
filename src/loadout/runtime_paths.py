"""Names a harness or the OS writes for itself, which neither init adopts nor the
unmanaged scan reports: shared by discovery and `unmanaged.py` (review 3, batch 4.4)."""

from __future__ import annotations

from .harnesses import DROID_AGENTS_DIRECTORY

RUNTIME_DIRECTORIES = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        "cache",
        "caches",
        "sessions",
        "projects",
        "history",
        "logs",
        "debug",
        "statsig",
        "telemetry",
        "shell-snapshots",
        "todos",
        "file-history",
        "session-env",
        "backups",
        "paste-cache",
        ".ssh",
        "secrets",
    }
)
RUNTIME_FILES = frozenset(
    {
        "auth.json",
        ".credentials.json",
        "credentials.json",
        "credentials",
        "history.jsonl",
        "history.json",
        "trust.json",
        "trusted-folders.json",
        "mcp-cache.json",
        "run-history.jsonl",
        "mcp-onboarding.json",
        "stats-cache.json",
        "session_index.jsonl",
        "known_marketplaces.json",
        "installed_plugins.json",
        "plugins_installation.json",
        ".env",
        ".env.local",
    }
)
# Written by the OS into any folder, never by a harness.
UNMANAGED_NAMES = frozenset({".DS_Store"})
# Entries a harness writes into a folder loadout writes too, exempt in that folder only:
# Claude Code's sync of the claude.ai account's skills, and Droid's mission droids. The
# evidence is in docs/reference/claude-code.md and droid.md.
HARNESS_RUNTIME_ENTRIES = frozenset(
    {
        "${CLAUDE_CONFIG_DIR:-~/.claude}/skills/synced",
        *(
            f"${{FACTORY_HOME_OVERRIDE:-~}}/.factory/{DROID_AGENTS_DIRECTORY}/{name}.md"
            for name in ("scrutiny-feature-reviewer", "user-testing-flow-validator")
        ),
    }
)
