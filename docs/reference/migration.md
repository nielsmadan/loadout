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

## What discovery reads

Discovery is an allowlist. Under each harness root it reads only the paths below, and everything
else there is left alone unread: never mapped, never reported, never a reason to stop. A `.DS_Store`
is skipped wherever it appears, including inside a listed tree. See
[0024](../decisions/0024-discovery-reads-an-allowlist.md). The table is `MANAGED_PATHS` in
`src/loadout/discovery.py`; a path ending in `/` is a tree.

Global scope:

| root | paths read |
|---|---|
| `${CLAUDE_CONFIG_DIR:-~/.claude}` | `CLAUDE.md`, `settings.json`, `mcp-permissions.json`, `skills/`, `hooks/`, `rules/`, `scripts/`, `providers/`, `profiles/`, `plugin/` |
| `${CLAUDE_CONFIG_DIR:-~}/.claude.json` | the file itself (only `mcpServers` is adopted) |
| `${CODEX_HOME:-~/.codex}` | `AGENTS.md`, `AGENTS.override.md`, `config.toml`, `hooks.json`, `rules/`, `skills/` |
| `${FACTORY_HOME_OVERRIDE:-~}/.factory` | `AGENTS.md`, `settings.json`, `hooks.json`, `mcp.json`, `skills/`, `plugins/`, `hooks/`, `rules/`, `scripts/`, `providers/`, `profiles/`, `plugin/` |
| `${XDG_CONFIG_HOME:-~/.config}/opencode`, or `$OPENCODE_CONFIG_DIR` | `AGENTS.md`, `opencode.json`, `opencode.jsonc`, `skills/`, `plugins/`, `hooks/`, `rules/`, `scripts/`, `providers/`, `profiles/`, `plugin/` |
| `$OPENCODE_CONFIG` | the file itself |
| `${PI_CODING_AGENT_DIR:-~/.pi/agent}` | `AGENTS.md`, `settings.json`, `mcp.json`, `extensions/pi-permission-system/config.json`, `skills/`, `plugins/`, `hooks/`, `rules/`, `scripts/`, `providers/`, `profiles/`, `plugin/` |
| `${XDG_CONFIG_HOME:-~/.config}/mcp/mcp.json` | the file itself (Pi) |
| `~/.agents` | `skills/`, `mcp.json`, `mcp/mcp.json` |

Project scope, relative to the repository root:

| root | paths read |
|---|---|
| `.claude` | `settings.json`, `mcp-permissions.json`, `skills/` |
| `.codex` | `rules/` |
| `.factory` | `settings.json`, `hooks.json`, `mcp.json`, `skills/` |
| `.opencode` | `skills/` |
| `.pi` | `extensions/pi-permission-system/config.json`, `skills/` |
| `.agents` | `skills/` |
| repository | `opencode.json`, `opencode.jsonc`, `.mcp.json`, and every `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`, `CLAUDE.local.md` and `opencode.md` outside the harness roots |

Two more kinds of read go beyond the table:

- A file that an adopted document references, such as a hook script named by a command, is read to
  confirm that it exists. It stays where it is.
- Global init in a directory that already holds a harness-shaped tree (`claude/`, `.claude/`, …)
  still reports that tree for confirmation.

Runtime state inside a listed tree, such as `node_modules/` or `.env`, is still excluded.
A file at a listed path that loadout cannot adopt, such as `opencode.jsonc` or a `.tmpl` inside a
skill, still blocks the preview; leaving it would let it shadow what loadout writes.

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
  state, credentials, commands and subagents keep working. A directory loadout writes nothing into,
  such as `commands/`, links back whole, so a file added to it later appears;
- the target's bytes are not changed. Its copies of the adopted files are simply no longer read.

The harness root is a real directory afterwards, so an entry added later directly in the target, or
inside a directory loadout writes into such as `skills/`, does not appear at the harness path.

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
