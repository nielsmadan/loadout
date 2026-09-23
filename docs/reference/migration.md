# Migration

`loadout init project` and `loadout init global` adopt configuration only when Loadout has a
known slice destination for it.

## What init adopts

- permissions and MCP tool policy
- instructions at the harness's standard instruction path
- settings owned by a supported settings slice
- hook and plugin declarations
- MCP server definitions
- skills
- global module configuration and support files

Project command trees, subagents and other harness-specific files are left in place. They keep
working because init neither moves them nor adds their paths to `.gitignore`.

## Source shape

Project init writes `loadout/config.toml` plus the canonical project slice sources it found.
Global init writes `loadout/loadout.toml`, canonical global slice sources, and registers that
directory in the machine config. Neither scope writes `artifacts.toml`.

Migration previews, privacy checks, Git staging, interruption journals, resume and recovery still
apply. Existing generated destinations are reconstructed in isolation before approval.

## Older adopted repositories

Nothing using artifact routes was published. Re-initialise repositories adopted by a route-based
development build: move command and subagent files from the Loadout source tree back to their
harness paths, remove `artifacts` and `presets` from project configuration, then run init again.

Sync appends current generated destinations to `.gitignore`. It leaves stale entries from older
versions alone because Loadout cannot distinguish its old entries from user-authored policy.

## Known limitation

Global `module-config` carries hook support files for harnesses with known global roots. Project
scope has no `module-config` destination. A Droid project hook that names a support script must
therefore keep that script at its harness path; init leaves it there.
