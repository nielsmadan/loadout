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

One more kind of read goes beyond the table. A file that an adopted document references, such as
a hook script named by a command, is read when it lies under a selected root. A reference that is
missing, outside the roots, dynamic (`$VAR/…`), a glob or a directory is left as written: the
adopted document keeps pointing at the original path, which keeps working because loadout does not
move it.

The selected directory is never scanned. A dotfiles copy there (`claude/`, `.claude/`, an installer,
a `Makefile`) is not read; init always adopts the live deployment. Runtime state inside a listed
tree, such as `node_modules/` or `.env`, is still excluded.

## What init refuses

Discovery has no option to point it at another layout, so every refusal names the file to change;
fix it on disk and run init again. An incomplete preview exits 2 and changes nothing.

| code | when |
|---|---|
| `agents-required` | no harness was given and none has configuration at a known path |
| `symlink-target`, `symlink-cycle` | a link at a read path is missing its target or loops |
| `external-symlink` | in project scope, a harness root links outside the repository; its content would become committed source. There is no override: share it as a loadout template, or replace the link with a real copy (`cp -RL`) and run init again ([0026](../decisions/0026-a-project-root-linked-outside-the-repository-has-no-override.md)) |
| `unadoptable-format` | `opencode.jsonc`: loadout writes `opencode.json` and cannot carry JSONC comments |
| `invalid-native-document` | a document at a read path does not parse |
| `dependency-activation` | an adopted declaration references a de-linked original instead of the copy loadout deploys |
| `source-composition` | two originals would write different content to one source file, such as a skill with the same name in two harnesses; make them identical or remove one. Also a Codex rules file holding a statement adoption cannot carry, quoted with its line; see [extraction](extraction.md#a-hand-written-codex-rules-file) |
| `source-exists`, `initialized-source`, `initialized-conflict` | the source directory already holds conflicting loadout source |
| `registration-conflict` | global: another source is registered; choose `--registration` |
| `reconstruction-failed` | the serialized source does not reproduce the originals |

Project `CLAUDE.md` and `AGENTS.md` holding different content stop init with *"project instruction
documents disagree across harnesses"* (exit 3, before any preview), since project scope keeps one
instruction source. A `CLAUDE.md` that is a symlink to `AGENTS.md`, or whose whole content is
`@AGENTS.md` (or `@./AGENTS.md`), surrounded by whitespace and optionally preceded by loadout's
banner, is the same document as `AGENTS.md`; anything more in it is a disagreement. In global
scope each harness keeps its own, so `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` may differ.

## Source shape

Project init writes `loadout/config.toml` plus the canonical project slice sources it found.
Global init writes `loadout/loadout.toml`, canonical global slice sources, and registers that
directory in the machine config. Neither scope writes `artifacts.toml`.

Migration previews, privacy checks, Git staging, interruption journals, resume and recovery still
apply. Existing generated destinations are reconstructed in isolation before approval.

When reading an existing permission document loses something, such as an entry a trailing `*`
shadows or a Codex `justification`, the preview names it, file first, before anything is
written. What each harness can lose is listed in [extraction](extraction.md#what-each-renderer-loses).

## Symlinked deployments

A global harness root that is a symlink, such as `~/.claude` pointing into a dotfiles repo, is
adopted, and so are stow-style per-file links inside it. Adoption **de-links**
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
    settings.json are no longer used. Entries added later to ~/dotfiles/claude's top level, or
    inside skills/, will not appear here.

Only links at or below a harness root are deployment links. A link above one — macOS's
`/tmp` → `/private/tmp`, a symlinked home directory, `~/.config` for OpenCode — is resolved, never
replaced, and loadout writes through it. A `~/.config` that links into a dotfiles repo therefore
receives OpenCode's generated files in that repo, and loadout's own machine config and write
records with them. The preview says so in one line per link:

    ~/.config is a symlink to ~/dotfiles/config; loadout writes through it, so the files it writes
    under ~/.config land in ~/dotfiles/config.

`sync` writes through the same way and prints nothing about it. A file that already exists in the
target and differs from what loadout last wrote is still refused as modified outside loadout.

Still refused: a symlink cycle, and in project scope a harness root linked outside the repository,
whose content would otherwise become committed project source. That refusal has no override; it
names the link's target and the two remedies, a loadout template or a real copy made with `cp -RL`.

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
