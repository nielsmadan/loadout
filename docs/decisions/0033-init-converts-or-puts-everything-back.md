# 0033 — Init converts or puts everything back

**Status:** accepted (2026-10-18, ruling D-10). Supersedes the success, kill and restore clauses
of [0028](0028-init-protects-an-adoption-with-a-backup-folder.md), which is left as written: init
still records a before-image of every path first, and its Git rules stand.

## Context

The 1.0 review asked whether the migration cluster is proportionate. `migration*.py` and
`init_workflow.py` came to about 4,100 lines against 3,550 at 0.9, and `migration_backup.py` alone
grew from 253 to 684. The growth was the undo of a finished adoption, `loadout init restore`. It had
to tell init's writes from everything that happened since, so it needed planned and recorded
shapes, a spent and a reduced backup, kept paths with reasons, and a repository counted as init's
only while every file in it was. Each review round found another case it got wrong. A backup that
only has to survive one run needs none of that.

## Decision

- **Success is final.** init deletes the backup when it completes. `loadout init restore` and
  undoing a finished init are removed. The source is uncommitted, so Git shows what init wrote.
- **A failure puts every path back** in the same process, Ctrl-C included: file bytes and modes,
  links, the Git index, and removing a repository or folder init created. Nothing is checked for
  later edits, because nothing else ran in between.
- **The skill is the fallback.** After a failure that was put back, and when the plan cannot
  complete, init recommends doing the conversion with the bundled `/loadout` skill. At a terminal
  it offers to copy the skill into each converted harness's user skills folder. Otherwise it prints
  `loadout integrate skill copy --harness NAME`, a plain copy outside any source that refuses
  rather than overwrite a differing copy. A refusal that only needs an option is not one the skill
  can resolve, so it names the option instead.
- **A run killed part way** leaves its backup marked, and the next init puts it back before
  anything else, asking at a terminal and refusing without one. This is the only recovery left.

## Consequences

- `migration*.py` and `init_workflow.py` went from 4,094 lines to 3,528; `migration_backup.py` from
  684 to 187.
- An adoption cannot be undone by loadout once it succeeds. The user reverts the source with Git,
  and puts the originals back from their own copies.
- Putting back a killed run overwrites edits made to its paths since. The prompt says so.
- An agent driving init without a terminal cannot put back a killed run. It has to ask the user to
  run init at a terminal.
