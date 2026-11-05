# 0050 — A marked block in the user's global gitignore, offered once and recorded

**Status:** accepted (2026-11-03, overlay run 1). Extends
[0046](0046-info-exclude-instead-of-a-committed-gitignore-for-overlay-targets.md).

## Context

Three overlay outputs are personal in every repository: `CLAUDE.local.md`,
`.claude/settings.local.json` and `.factory/settings.local.json`. No team commits them. Listing them
in every target's `info/exclude` works, but a user who works in many repositories usually wants
them ignored everywhere, including by tools that read the global ignore file. Every other overlay
path is one that some team commits (`.codex/config.toml`, `opencode.json`, skill folders), and a
global entry for those would hide a new file at that path in every repository on the machine,
loadout-adopting ones included.

## Decision

- **Sync offers once, interactively, to add the three paths to the global ignore file**
  (`core.excludesFile`, else `$XDG_CONFIG_HOME/git/ignore`). The offer names the file, says
  whether it is inside a git repository, and says the other paths stay ignored per repository.
  It is never made without a terminal; `check` prints a hint instead.
- **The answer is recorded** in `overlay-global-ignore.json` beside `overlays.toml`, not in an
  overlay record: `accepted` or `declined`, and the file. A declined offer is not repeated, and
  the paths go to `info/exclude` as before.
- **The entries are a marked block** (`**/CLAUDE.local.md` and so on), recomputed from what every
  overlay record holds, under one lock held across the read and the write. It shrinks when no
  overlay writes a path, and the block is removed when none does.
- **A pattern the user wrote there counts and is never touched.** A path the file already ignores
  outside the block is left out of it, found by asking git, not by reading lines.
- **A path the global file covers is also left out of `info/exclude`.** A path the global file does
  not cover stays in it, so declining costs nothing but the repetition.

## Consequences

- The global ignore file is a user file, possibly inside a dotfiles repository, where the block
  shows as a change. The offer says so.
- A failure to write the block is reported (exit 3) after the overlay's own work is done and
  recorded.
