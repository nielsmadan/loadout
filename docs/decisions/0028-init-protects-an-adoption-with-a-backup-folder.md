# 0028 — Init protects an adoption with a backup folder

**Status:** accepted (2026-10-14). Supersedes the journalled transaction that
[0023](0023-adoption-de-links-a-symlinked-deployment.md) measured de-linking through, and its
consequence that `init resume` does not repeat the de-link notice.

## Context

`init` protected an adoption with a Git checkpoint commit of the untracked originals on the user's
branch (and `git init` where there was no repository), a journalled transaction that
`init resume <journal>` finished and `init recover <journal>` undid, and staging of the result into
the user's index. That came to about 2,300 lines of code and 1,000 of tests. It committed to the
user's branch, created a repository in a project that had none (against the 2026-09-18 ruling), and
staged content the user had not reviewed as a diff.

Every effect `init` has is a change to a path: a file's bytes and mode, a symlink, a directory, or
its absence, including the Git index when `init` untracks generated outputs. A before-image of
those paths undoes all of it. The journal's own image model already recorded exactly that.

## Decision

Before its first write, `init` records a before-image of every path it will touch in
`$XDG_STATE_HOME/loadout/backups/<timestamp>/`: the operations it applies, the first render's outputs,
the deployment receipt, the write record, the source's new repository, and the Git index when it
untracks outputs. Each missing ancestor a write could create is recorded as absent. A manifest maps
each entry to its path, and an in-progress marker is cleared on success.

- **`init` fails:** it restores the before-image itself and says so.
- **`init` is killed:** the next `init` finds the in-progress backup and offers to restore it;
  `--json` reports it as `interrupted`. `loadout init restore --yes` restores it.
- **Success:** the backup is kept, and only the latest completed one. `loadout init restore` undoes
  the adoption from it. It records what each path held after `init`, so a path changed since is
  kept and reported instead of overwritten, and a directory `init` created goes only once empty.
- **Git:** no checkpoint commit, no `git init` in a project, and no staging. The index changes only
  to untrack what `init` adds to `.gitignore`. The global source is loadout's own directory, so it
  still gets a repository; committing it is suggested, never done.
- `init resume`, `init recover`, the journal and the checkpoint are removed.

## Consequences

- An interrupted `init` is rolled back and run again, never resumed.
- A path beneath a symlink `init` replaces is restored as part of the link, never written through
  it, so de-linking a dotfiles deployment and undoing it leave the dotfiles target untouched.
- Restoring a completed backup after later edits is partial by design: changed paths stay.
- Like the journal, a before-image keeps content, mode and kind, not modification times, extended
  attributes, ownership or hard-link identity.
- The source and its outputs are left uncommitted and unstaged for the user to review as an
  ordinary diff.
