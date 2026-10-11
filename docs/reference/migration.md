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

## Symlinked deployments

A global harness root that is a symlink, such as `~/.claude` pointing into a dotfiles repo, is
adopted without `--mapping`, and so are stow-style per-file links inside it. Adoption **de-links**
them; see [0023](../decisions/0023-adoption-de-links-a-symlinked-deployment.md):

- loadout's outputs become real files at the harness path;
- every entry loadout does not manage becomes a link back into the original target, so runtime
  state, credentials, commands and subagents keep working;
- the target's bytes are not changed. Its copies of the adopted files are simply no longer read.

The preview lists each link as `Replace symlink: <link> -> <target>`, and `--json` lists it under
`delinked`. After applying, init says what it did:

    ~/.claude was a symlink into ~/dotfiles/claude. Loadout's files are now real files here;
    everything else still links into ~/dotfiles/claude. ~/dotfiles/claude's copies of CLAUDE.md,
    settings.json are no longer used.

Only links at or below a harness root are deployment links. A link above one — macOS's
`/tmp` → `/private/tmp`, a symlinked home directory, `~/.config` for OpenCode — is resolved, never
replaced, and loadout writes through it. A `~/.config` that links into a dotfiles repo therefore
receives OpenCode's generated files in that repo.

Still refused: a symlink cycle, and in project scope a harness root linked outside the repository,
whose content would otherwise become committed project source. The latter takes an explicit
`--mapping`.

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
