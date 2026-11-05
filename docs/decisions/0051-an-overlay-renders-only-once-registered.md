# 0051 — An overlay renders only once registered

**Status:** accepted (2026-11-03, overlay run 1). Extends
[0031](0031-global-scope-renders-only-from-the-registered-source.md) to `overlays.toml`.

## Context

An overlay writes into repositories and into `~/.claude.json`, outside any folder it owns. A
`loadout.toml` with an `[overlay]` table in a cloned repository, or in a folder a pull request
adds, must not become a way to write there. 0031 closed that door for the global scope by making
registration the trust decision.

## Decision

- **An overlay folder renders only when `~/.config/loadout/overlays.toml` registers it**, through
  `loadout overlay add`. The file is machine state under `config_dir()`, kept apart from the
  machine config so that file's parser is untouched. Each entry has a name and an absolute path
  resolved at registration.
- **Anywhere else a folder holding an `[overlay]` table is refused**, naming the command that
  registers it. A folder that is the registered global source is refused as an overlay.
- **Selection follows registration.** `--overlay NAME` and a registered folder as the working
  directory select an overlay; a repository is a target only through a registered overlay's
  patterns.
- **Removal works from the record alone.** `overlay remove` marks the registration `removing`,
  retires everything the overlay's record lists, and deletes the record and the registration. A
  folder that was deleted can still be removed by name.

## Consequences

- Registering an overlay is the one step that grants it write access to the paths its patterns
  match. There is no flag that registers as a side effect of syncing.
- A record with no registration (the file was edited by hand) is listed by `overlay list` and
  removed with `overlay remove NAME`.
