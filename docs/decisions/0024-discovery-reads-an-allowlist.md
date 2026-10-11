# 0024 — Discovery reads an allowlist

**Status:** accepted (2026-10-11).

## Context

Discovery walked every entry under a harness root and excluded a list of known runtime paths. Any
other entry needed a category, so a plain `~/.claude` holding a `.DS_Store`, a `notes.txt` or
Claude's own `ide/1.lock` made `init global` exit 2 with `source-mapping`. A real `~/.claude` holds
dozens of such entries, and Claude adds new ones from release to release.

The classifier also matched file names anywhere under any root. So `config.toml` or `tui.json`
inside `~/.claude` became Claude settings, and `unknown/CLAUDE.md` became Claude's instructions.

## Decision

Discovery reads only an allowlist of paths per scope and harness: the files and trees loadout
writes, and the module-config trees it already carried. Everything else under a harness root is
left alone unread: never mapped, never reported, never a reason to stop. `.DS_Store` is skipped
everywhere. The list is `MANAGED_PATHS` in `src/loadout/discovery.py`, documented in
`docs/reference/migration.md#what-discovery-reads`.

A user-supplied mapping is outside the allowlist, since it states what its root holds.

## Consequences

- When a root is de-linked (0023), an entry discovery did not read is linked back into the target
  as a whole. A directory loadout writes nothing into, such as `commands/`, is now one link rather
  than a directory of per-entry links, so a file added to it later does appear. This supersedes
  the `commands/` part of 0023's fourth consequence.
- A cross-harness name, such as `config.toml` in `~/.claude`, is left in place rather than merged
  into another harness's settings. `settings.local.json`, `CLAUDE.local.md`, `keybindings.json`,
  Pi's `models.json` and OpenCode's `tui.json` are left in place, because loadout writes none of
  them.
- In project scope, `hooks/`, `rules/` (except Codex's), `commands/` and `agents/` were already left
  in place. They are now not read either, so they no longer appear in the preview.
- A file at a listed path that loadout cannot adopt, `opencode.jsonc` or a `.tmpl`, still refuses.
