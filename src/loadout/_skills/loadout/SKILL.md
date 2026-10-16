---
name: loadout
description: Use when adopting existing agent configuration into Loadout, setting up Loadout in a project or global source, or changing Claude, Codex, Droid, OpenCode or Pi configuration. Handles onboarding, permissions, instructions, skills, settings, hooks, plugins and MCP through their authoritative sources.
---

# Loadout

Edit the declared source for each configured consumer. Generated harness files are outputs.

## Instructions

1. For setup or migration, read [onboarding](references/onboarding.md). Preview with
   `loadout init project --dry-run --json` or `loadout init global --dry-run --json`, resolve
   harnesses and ownership, then apply the approved
   plan. Never execute discovered scripts or installers.
2. For ordinary changes, read [configuration routing](references/configuration.md). Default to
   personal configuration for the current project; explicit project/global scope takes precedence.
   Locate the existing config and follow its declared producers: the manifest's agent blocks and
   the source slices they name. Ask before widening scope or changing a source shared by other
   consumers.
3. Apply a generic request to configured harnesses with a sound mapping. Report unsupported consumers.
   Keep MCP server definitions separate from MCP tool-approval policy. Preserve ordering, comments,
   private sources and unrelated fields; never create overlapping producers.
4. Sync the edited source: `loadout sync --root <repo>` for project/personal changes;
   `loadout sync --global` for the active global profile, or
   `loadout sync --global --profile <name>` when explicitly selected. Ask whether an unnamed change
   under a non-default profile belongs to that profile or its inherited default.
5. Report source files changed, agents reached, unsupported consumers and the sync result.

## Examples

- “Set up Loadout here” previews discovery, resolves the scope and configured harnesses, and shows
  writes and removals before applying. Success leaves editable, uncommitted source and a backup.
- “Allow `just test` for me” edits the existing personal permissions producer and syncs. If the
  project has no personal producer, ask before changing committed ownership.
- “Change the global Pi model” follows the settings fragment Pi's agent block names, edits its
  `defaultModel` value and syncs the selected profile.

## Troubleshooting

- An unresolved preview needs an explicit scope, a harness choice, or a fix to the file it names;
  `--yes` only approves a resolved plan. Retain unsupported inputs and report their paths.
- A missing personal producer requires a scope decision. Do not silently edit shared source or
  invent a second contributor claiming the same keys.
- A change made outside loadout belongs in the source, not beside it. When sync refuses an output,
  or `check` reports `hand-edited`, `owned-key`, `owned-key-removed`, `unknown`, `unmanaged`,
  `unmanaged-link` or `unparsable`, follow
  “Porting a change made outside loadout” in [configuration routing](references/configuration.md).
  Run `sync --force` only after showing the diff and getting the user's approval. A secret in a
  diff prints as `<redacted>`; never port that placeholder into the source, or `--force` writes it
  over the real token. Read the real value from the file, or move it to an environment variable.
- An init that lists `unadopted` items left copies that disagree across harnesses in place. Merge
  them by “Resolving conflicting copies at init” in [onboarding](references/onboarding.md).
- An init that lists `secrets` adopted them into the source as they were. Say the source's remote
  must stay private and offer to move each into an environment variable, by “Secrets in the adopted
  source” in [onboarding](references/onboarding.md).
- An interrupted init reports its backup. Ask before restoring it with `loadout init restore`,
  as the onboarding reference describes; a failed init has already restored itself.
- In a linked git worktree of the registered global source, the checkout and merge hooks print
  that nothing was synced: a worktree is not the registered folder, so it never renders global
  scope. Edit and commit there as usual (its pre-commit check still runs), then sync from the
  registered source once the change reaches it.
