# 0045 — An overlay source renders into repositories that have not adopted loadout

**Status:** accepted (2026-11-03, overlay run 1). Triggers the "Revisit only if that adoption
model ever changes" line in [scopes](../scopes.md); that text is left as written and points here.
The consequence that a target can also be a project is superseded by
[0052](0052-a-target-that-adopts-loadout-is-retired-by-the-overlay.md).

## Context

Scopes assumed all-or-nothing adoption: a repository either has a `loadout/` source, and loadout
generates and gitignores its outputs, or loadout does not touch it. A developer who works in
team repositories that have not adopted loadout, and who cannot commit a `loadout/` folder there,
had only the global scope. It writes under the home directory and reaches no repository.

An **overlay source** is a registered folder whose `loadout.toml` carries an `[overlay]` table. It
renders personal, never-committed configuration into the repositories its `targets` patterns
match. It is a new kind of source, not a project: the repository holds nothing of loadout's, and
the team's tracked files are never written.

## Decision

- **Additive destinations only.** Each artifact goes to a destination the harness merges over the
  team's configuration, or to a path the team does not track. A slice with neither (hooks,
  plugins, agents, module config, Codex defaults, Claude's `mcp-permissions`) is a configuration
  error that says why. A path the team tracks is refused, never replaced.
- **Shared documents are owned by key and by entry, never whole**. `.claude/settings.local.json`,
  `.factory/settings.local.json` and the root `opencode.json` may already hold the user's or the
  harness's keys. The overlay owns the keys it renders, each with a digest, and the individual
  rules it wrote into Claude's `permissions.allow`/`ask`/`deny` and Droid's three command lists.
  Everything else in the file is left alone, and an approval the harness saved is carried.
  Every other destination is a whole file the overlay owns; a file already there that loadout
  never wrote is refused until `--force`.
- **Codex rules go to `.codex/rules/<overlay>.rules`.** Codex reads the folder whole in a trusted
  project, so a second file beside the team's applies without touching it.
- **A server name the user already defined is refused.** Codex merges `mcp_servers` per field, so
  a same-named project server would inherit stale fields of the user's. Before anything is
  rendered, the overlay's server names are compared with the user-level files of the harnesses
  that render servers: Codex's `config.toml`, Droid's `mcp.json` (whose merge with a project file
  is not established, so it is refused on the safe side), OpenCode's `opencode.json`/`.jsonc` and
  `OPENCODE_CONFIG`, and Pi's `mcp.json`. A match fails the whole overlay run with exit 3. For
  Claude the same rule applies per server in `~/.claude.json`; see
  [0048](0048-nested-json-ownership-claude-json-leaves-and-owned-keys-in-shared-documents.md).
- **The registry name shapes a filename and a marker**: `.codex/rules/<name>.rules` and the
  `info/exclude` block's lines. It is machine state in a generated file's *name*, outside
  [0008](0008-generated-files-carry-no-machine-state.md)'s reach, which covers file contents. Two
  machines that register the same overlay under different names produce different filenames and
  the same contents.

Registration is the trust decision, as in [0051](0051-an-overlay-renders-only-once-registered.md).

## Consequences

- A repository can be targeted by several overlays, and can also be a project or the global
  source. Each file has one owner: a path another overlay or a project records is refused.
- Retirement is scoped to the run's targets and needs positive evidence that a target is gone.
- Worktrees, a git config hook and branch-switch re-sync are not part of this decision. The
  `follow_worktrees` and `git_hook` keys are reserved and refused when true.
