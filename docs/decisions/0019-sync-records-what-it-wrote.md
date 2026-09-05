# 0019 — `sync` records what it wrote

**Status:** accepted (2026-09-05, milestone 5).

## Context

[0008](0008-generated-files-carry-no-machine-state.md) kept machine state out of generated
files and left `sync`'s modified-outside-loadout guard stateless: it compares the file on disk
against every output loadout *could* produce — the committed source and the working tree, under
every declared profile. It named the cost precisely:

> Correct, at the cost of warning benignly on first adoption and on a reverted-but-already-synced
> edit. Both resolve with `--force`.

And it named the fix, and the order:

> The sidecar is deferred, not rejected. **Orphan removal is what forces it** … Build it there,
> then move the sync check onto it.

**The cost was underestimated, and orphan removal is not what forced it.** Two cases 0008 does
not enumerate:

- **Editing a source twice before committing.** Edit, sync, edit, sync — the loop everyone
  works in, with a commit at the end. The file on disk was rendered from a state that is neither
  HEAD nor the working tree. Nothing is wrong and `sync` refuses. With two sources composing into
  one destination it needs even less: edit one, sync, edit the other.
- **A skill's supporting file.** The copied branch consulted no accept set at all, comparing the
  destination against the *current* source. The first edit blocked, and committing did not help
  because HEAD was never consulted on that path. `--force` was not a resolution but the only
  exit, permanently.

Measured across this machine's `~/ac` sessions: 9 of 19 hit the abort, and every one of them
then used `--force` — more often than the abort fired. A guard routed around by reflex protects
nothing, and the one time the edit is real it will be discarded by the same reflex.

## Decision

`sync` records what it wrote, per destination, and the guard accepts a match as a **third
variant** beside the two renders.

- **Where.** `$XDG_CONFIG_HOME/loadout/written/<hash of resolved root>.json`, resolved through
  `machine.machine_state_dir` — the same place [0010](0010-a-machine-config-locates-the-global-source.md)
  put the machine config. The keys are absolute destination paths, so the record is machine
  state and belongs nowhere in a source repo, gitignored or not.
- **What.** Hashes, not bytes. Text records the raw digest and a **normalised** digest; the
  normalised one is compared, because a harness reordering keys in a file it shares with loadout
  is not a hand edit. A copied file records raw bytes and its exec bit. A **merged** destination
  records its owned key set and **no hash** — the applied document embeds bytes the harness may
  rewrite a second later, so a hash of it would go stale with nothing wrong.
- **When.** In `cmd_sync`, after the write. `--force` records too, which is what turns a copied
  file's `--force` into the last one rather than a standing requirement. `check` never records.
- **Retention.** Entries are kept, not pruned. A destination no longer rendered is exactly what
  orphan removal needs, and pruning here would discard it at the moment it became interesting.

**It only widens acceptance.** Every check is `entry is not None and the hash matches`, so a
missing, corrupt or version-bumped record reproduces the two-variant behaviour exactly, warning
included. Losing state fails closed; there is no path where a hand edit is accepted because the
record went away.

## Consequences

- 0008 stands as written — it is append-only, and its reasoning about *generated files* is
  untouched. This delivers the sidecar it deferred, earlier than it expected and for a different
  reason.
- **Orphan removal is now a consumer away, not a mechanism away.** The record already holds every
  path loadout has written, and a merged destination's owned keys — which is what removal needs to
  strip a key rather than delete a file loadout does not own.
- `check` and `sync` no longer contradict each other. `check` asks the guard the same question
  `sync` will and says which answer applies, rather than ending every drift with "run `loadout
  sync`" even where sync is about to refuse.
- `AGENTS.md`'s "`machine.py` … the only place machine state is *stored*" stops being true, and
  is corrected in the same commit.
- `record.py` is untouched and deliberately not reused. It records owned key *names* beside a
  fragment **in the source repo**, committed, so two machines converge. Same word, opposite
  location and lifetime.
- A future change to the normaliser silently invalidates stored digests. That degrades to the
  two-variant set — one warn-and-force cycle after upgrading, never a wrong acceptance.
- Concurrent syncs of one root: last writer wins the record. The same one-process assumption
  `resolve.py`'s source cache already relies on.
