# 0047 — Writing `info/exclude` inside `.git` despite `containment.PROTECTED`

**Status:** accepted (2026-11-03, overlay run 1).

## Context

`containment.PROTECTED` names `.git`, and no output path loadout renders may sit inside it. The
overlay needs one write there: the repository's `info/exclude`, which is how
[0046](0046-info-exclude-instead-of-a-committed-gitignore-for-overlay-targets.md) hides outputs.

## Decision

- **The exclude block is the only exception, and `exclude.py` is the only code that makes it.**
  `PROTECTED` still refuses every rendered output path; the exception is a function that takes a
  git common directory and edits `info/exclude` under it. It refuses a symlink anywhere between
  the common directory and the file, before reading and again before the rename. It creates
  `info/` when missing and never the git directory itself.
- **Only lines between loadout's own markers are edited.** Every other line, and the file's mode,
  survive. A block that appears twice, or opens without a closing line, is an error naming the
  file; nothing is guessed.
- **One `flock` per file, keyed by its resolved path**, held across the whole read-modify-write
  and released after an atomic replace. Git reads `info/exclude` and takes no lock on it: `dir.c` only reads it, and
  the file is written only when `git init` or `git clone` copies the default template, so there is
  no lock to join and none is claimed.
  `info/exclude.lock` is not used.
- The common directory, not the worktree's `.git`, is edited, since `info/exclude` is shared by
  all of a repository's worktrees.

## Consequences

- The edit is durable (the file is synced before the rename) and keeps the file's mode.
- A hand-written line in the file is never altered. Adding and then removing a block restores the
  file byte for byte only when it ended with exactly one newline: the block is separated from the
  text before it by one blank line, and removing it removes that line again.
