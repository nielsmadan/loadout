"""The variables that redirect where loadout or a harness reads and writes. The suite
and the benchmark helpers clear the same ones, so neither can reach a real config."""

from __future__ import annotations

HARNESS_VARIABLES = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "XDG_CONFIG_HOME",
    "XDG_STATE_HOME",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "FACTORY_HOME_OVERRIDE",
    "LOADOUT_TEST_HARNESS_C",
)
