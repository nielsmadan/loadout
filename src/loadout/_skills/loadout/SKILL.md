---
name: loadout
description: Use when changing AI coding-agent configuration in a repository or on this machine, especially where loadout may own generated harness files.
---

# Loadout

Change loadout's authoritative source, never a generated Claude, Codex, OpenCode, or Pi file.
Read [the configuration reference](references/configuration.md) before choosing a source.

## Instructions

1. Resolve the scope. No flag means `--personal` for the current project; the alternatives are
   `--project` and `--global`. Accept at most one scope.
2. Locate the project root or machine config, then read the selected loadout config and relevant
   fragments. For `--global`, resolve the actual `XDG_CONFIG_HOME` environment value before
   inspecting a machine config: use `$XDG_CONFIG_HOME/loadout/config.toml` when it is non-empty,
   otherwise use `~/.config/loadout/config.toml`; never probe both. If that scope is not
   initialized, report the matching `loadout init` command and make no changes.
3. Identify configured agents from the selected source. A generic request applies to every
   configured agent with a sound mapping; an agent-specific request applies only to that agent.
   Treat MCP server definitions as `mcp`; tool approval for those servers is the separate
   `mcp-permissions` artifact.
4. Choose the narrowest existing authoritative fragment. Preserve comments, ordering, naming
   conventions, and unrelated content. Use an existing mechanical command such as
   `loadout harness add` or `loadout template add` when it exactly represents the request.
5. Run `loadout sync --root <repo>` for personal/project scope. For global scope, run
   `loadout sync --global --profile <name>` when the request explicitly names a profile;
   otherwise run `loadout sync --global` for the active profile.
6. Report the source files changed, agents reached, unsupported agents, and sync result.

## Ask before writing

- Personal scope cannot represent the requested artifact: stop and end with one direct question
  asking whether to use the applicable project or global scope. Never widen personal
  configuration into committed or machine-wide configuration without confirmation.
- A non-default global profile is active and the user did not name a profile: ask whether the
  change belongs only to that profile or to the default inherited configuration.
- Competing mappings would materially change behavior, or an agent-specific change would alter a
  shared fragment consumed by other agents: show the outcomes and ask.

Partial agent support is not ambiguous: update supported configured agents and report exclusions.
Global settings have sound mappings for Claude and OpenCode through `settings`, and for Codex
through its `defaults` slice. Report configured Pi agents as unsupported rather than inventing or
editing a harness-owned settings file.
If sync refuses a generated file changed outside loadout, report the conflict and leave the source
edit visible. Never use `--force` unless the user explicitly requests it.

## Examples

- “Allow `just test` for me” edits `loadout/permissions.local.toml` and syncs the project.
- “Add this instruction to the project” edits a committed project instruction fragment and syncs.
- “Change the global OpenCode model” edits the selected global settings fragment and syncs the
  active global profile.

## Troubleshooting

- If the selected scope is unsupported, make no changes and ask about the narrowest supported
  scope.
- If global configuration is absent, report `loadout init --global` and stop.
- If sync reports an externally modified output, preserve both the source edit and the output;
  report the conflicting path instead of forcing the sync.
