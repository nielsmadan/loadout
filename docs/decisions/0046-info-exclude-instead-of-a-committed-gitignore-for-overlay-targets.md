# 0046 — `info/exclude` instead of a committed `.gitignore`, for overlay targets only

**Status:** accepted (2026-11-03, overlay run 1). Narrows the project-scope choice recorded in
[scopes](../scopes.md), which is left as written.

## Context

Project scope ignores its outputs in a committed `.gitignore`, because `.git/info/exclude` is
per-clone and does not apply on a fresh checkout. That choice assumed the repository has adopted
loadout. An overlay target has not: a `.gitignore` entry there would be a change to the team's
tracked files, naming paths only one developer generates.

## Decision

- **An overlay target's outputs are ignored through the repository's own `info/exclude`**, in a
  block per overlay between marker lines, `# >>> loadout overlay NAME: managed by loadout; …` and
  `# <<< loadout overlay NAME`. Lines are anchored (`/CLAUDE.local.md`, `/.claude/skills/review/`)
  and end with `.loadout-tmp-*` so a temporary file of an interrupted write never shows.
- **A path the user's global ignore file already ignores is left out of the block.** Only the
  global file counts, found by `git check-ignore -v` naming it as the source: a rule in the
  branch's `.gitignore` changes with the branch.
- **Verified after the block is written.** Sync asks `git check-ignore --no-index` about every
  unit it is about to write. A unit git would still show, because a later `!` line re-includes it
  or nothing ignores it, is refused with exit 3 and the line that re-includes it; the file is not
  written.
- **Order:** the block is written before any file, and shortened only after a file is deleted.
  An interrupted run can leave a path ignored that does not exist, never one that exists and shows.
- Personal paths that are the same in every repository (`CLAUDE.local.md`,
  `.claude/settings.local.json`, `.factory/settings.local.json`) may also go to the global ignore
  file; see [0050](0050-a-marked-block-in-the-users-global-gitignore-offered-once-and-recorded.md).

## Consequences

- The block is per clone. A fresh clone has none until the next sync there; nothing is committed
  either way.
- `git status` in a target stays clean, and the team's `.gitignore` is not touched.
- Writing the block is a write inside `.git`; see
  [0047](0047-writing-info-exclude-inside-git-despite-containment-protected.md).
