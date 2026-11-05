# 0048 — Nested JSON ownership: `~/.claude.json` leaves and owned keys and rules in shared documents

**Status:** accepted (2026-11-03, overlay run 1). Extends the per-key ownership of
[0017](0017-ownership-may-be-declared-instead-of-derived.md) and
[0019](0019-sync-records-what-it-wrote.md), which cover top-level keys, to nested paths.

## Context

An overlay writes MCP servers for Claude Code into `~/.claude.json`, at
`projects.<repository>.mcpServers.<name>`. That file is Claude Code's own: it holds trust
decisions, history, usage counters and the servers added with `claude mcp add`, whose default
scope writes the same map. Claude rewrites the file whenever it saves state. The overlay also
shares three project documents with their harness (`.claude/settings.local.json`,
`.factory/settings.local.json`, the root `opencode.json`). `surgery.apply_json` and
`written.owned_values` own top-level keys only.

## Decision

- **Ownership is a path of key segments, never a dotted string.** A repository path contains `/`
  and `.`, so `projects./Users/me/app.v2.mcpServers.docs` cannot be split back. A path is a tuple
  and is stored as a JSON list. `json_paths.py` holds the lookup, set and remove for it.
- **The unit in `~/.claude.json` is one server**, the leaf `projects.<root>.mcpServers.<name>`.
  Retirement removes that leaf. It never removes a whole project entry, which is not loadout's,
  and it leaves the `mcpServers` map in place.
- **Each owned leaf and key has a digest of what loadout wrote.** A value that matches neither the
  digest nor the wanted value was edited outside loadout: `owned-key`, refused until `--force`. A
  missing one is `owned-key-removed`. A server of that name the overlay never wrote is
  `user-defined`, refused with `--force` too, so a user's own server is never replaced.
- **In the three project documents the unit is a key, or one rule.** Claude's
  `permissions.allow`/`ask`/`deny` and Droid's three command lists are owned entry by entry, so the
  entries Claude saves there with "don't ask again" are carried, not overwritten . Everything else in the document is never judged, written or retired.
- **Pending digests make a crash recoverable.** The leaves about to be written are recorded as
  pending, with their digests, before the file is touched. A present leaf equal to a pending or
  wanted value is adopted rather than reported as the user's. A crash between the record and the
  write leaves a record that over-claims, never one that under-claims.
- **The write joins Claude Code's own lock.** Claude Code 2.1.317 saves the file under
  proper-lockfile: the installed binary contains `lockfilePath:` with a template literal of the
  config path plus `.lock`, and `stale:1e4`, so the lock is the directory `<path>.lock` made with
  `mkdir`, taken over after ten seconds. Loadout takes its own `flock` first, then makes that
  directory at the path Claude derives (the config directory joined with `.claude.json`, no
  `realpath`), waits at most 15 seconds with a doubling back-off, removes the directory only if its
  inode is still the one it made, and compares the file's bytes again just before the rename,
  retrying once. It never breaks either lock.
- **Claude Code's own derivation is followed in two refusals.** A legacy `.config.json` that
  Claude reads instead of `.claude.json`, and `CLAUDE_CODE_CUSTOM_OAUTH_URL`, which moves its file to
  `.claude-custom-oauth.json`, make loadout refuse to write rather than write a file Claude does
  not read.

## Alternative set aside

**Shelling out to `claude mcp add-json --scope local`.** It would let Claude write its own file.
It was set aside because the global scope already edits `~/.claude.json` directly, so a second
mechanism would give two ways to be wrong about the same file; because it needs Claude Code
installed and one process per server per repository; and because it records nothing about what
loadout wrote, so retirement and the edited-outside check would need the same digests anyway.

## Consequences

- Known limit: a number such as `1.23e-05` in a part of `~/.claude.json` the overlay does not own
  is rewritten as Python spells it (`1.23e-05`) where Node wrote `0.0000123`. The value is the
  same. The global scope has the same limit.
- Known limit: a document holds one digest per key. A crash after a document write, followed by an
  edit of the source before the next run, can read as an edit outside loadout (exit 1); `--force`
  clears it.
- Loadout's own lock times out after 30 seconds and names the holder's PID; Claude's after 15
  seconds, and the message tells the user to close the session that is saving. Neither message
  tells the user to delete a lock.
