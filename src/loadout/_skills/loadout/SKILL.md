---
name: loadout
description: Use when adopting existing agent configuration into Loadout, setting up Loadout in a project or global source, or changing Claude, Codex, OpenCode or Pi configuration. Handles onboarding, permissions, instructions, skills, settings, hooks, plugins and MCP through their authoritative sources.
---

# Loadout

Edit the declared source for each configured consumer. Generated harness files are outputs.

## Instructions

1. For setup or migration, read [onboarding](references/onboarding.md). Preview with
   `loadout init --dry-run --json`, resolve scope, agents and ownership, then apply the approved
   transaction. Never execute discovered scripts or installers.
2. For ordinary changes, read [configuration routing](references/configuration.md). Default to
   personal configuration for the current project; explicit project/global scope takes precedence.
   Locate the existing config and follow its declared producers, including native artifact parts
   and trees. Ask before widening scope or changing a source shared by other consumers.
3. Apply a generic request to configured agents with a sound mapping. Report unsupported consumers.
   Keep MCP server definitions separate from MCP tool-approval policy. Preserve ordering, comments,
   private sources and unrelated fields; never create overlapping producers.
4. Sync the edited source: `loadout sync --root <repo>` for project/personal changes;
   `loadout sync --global` for the active global profile, or
   `loadout sync --global --profile <name>` when explicitly selected. Ask whether an unnamed change
   under a non-default profile belongs to that profile or its inherited default.
5. Report source files changed, agents reached, unsupported consumers and the sync result.

## Examples

- “Set up Loadout here” previews discovery, resolves the scope and configured agents, and shows
  checkpoint paths and removals before applying. Success leaves editable source and a staged migration.
- “Allow `just test` for me” edits the existing personal permissions producer and syncs. If a
  migrated native route has no personal producer, ask before changing committed ownership.
- “Change the global Pi model” follows the declared native settings part when present, edits its
  `defaultModel` value and syncs the selected profile.

## Troubleshooting

- An unresolved preview needs an explicit scope, mapping, source selection or ownership decision;
  `--yes` only approves a resolved plan. Retain unsupported inputs and report their paths.
- A missing personal producer requires a scope decision. Do not silently edit shared source or
  invent a second contributor claiming the same keys.
- External output drift requires reconciliation. Preserve the source edit and output, report the
  path, and never force sync without an explicit request to discard the external edit.
- An interrupted init reports a protected journal. Use the onboarding reference to resume or
  recover; successful baseline commits remain.
