# 0008 — Generated files carry no machine state; ownership records go in a sidecar

**Status:** accepted (2026-08-08, milestone 5)

## Context

`sync` needs to answer "did something other than loadout write this file?" before it
overwrites. Two places could hold the answer: inside the generated files, or beside them.

Embedding is the tempting one, because two output formats already carry a header —
`codex/rules/permissions.rules` and the instruction documents. Stamping the active profile,
the source commit, or a content hash into that header looks like a small extension.

It is not available uniformly. The three JSON outputs — `claude/settings.json`,
`opencode/opencode.json`, `pi/permissions.json` — have no comment syntax, so a stamp there
would have to be a real key in a document handed to five harnesses with no shared answer for
what an unrecognised key does. Those three are also the co-owned files, where the question
matters most.

The deeper objection applies to the formats that *can* hold a comment. Byte-identical output
against a live system is the acceptance criterion ([0003](0003-port-byte-identical-before-changing-behaviour.md)),
and it works because content is a pure function of the source. Machine state is not part of
the source. A stamped profile makes two machines rendering the same source produce different
bytes; a stamped commit churns every output on every commit. Commit `76be22e` had to rewrite
five golden fixtures because the existing header's paths moved — that is the failure mode
already, at its smallest.

## Decision

**Generated files contain only what the source determines.** No profile, no commit, no hash,
no timestamp, no machine identity — in a header or in a key. The existing headers stay as they
are: a fixed warning, not a metadata channel.

Where loadout needs to record what it wrote, that record goes in a **sidecar** outside the
generated files, gitignored, holding path → content hash, profile, and source commit.

## Consequences

- Output stays reproducible from the source alone, so goldens keep working and two machines
  agree byte for byte.
- `sync`'s modified-outside-loadout check cannot use recorded state, so it compares against
  every output loadout could have produced — rendered from the committed source and the
  working tree, under every declared profile. Correct, at the cost of warning benignly on
  first adoption and on a reverted-but-already-synced edit. Both resolve with `--force`.
- The sidecar is deferred, not rejected. **Orphan removal is what forces it**: a target
  deleted from the manifest leaves its output and destinations on disk forever, and no amount
  of re-rendering reveals a file loadout no longer generates. Build it there, then move the
  sync check onto it.
- The sidecar does not address `preserve` reading the in-repo output rather than the
  destination. That is a path question, not a state question, and still needs its own fix
  before the staged copies can be removed.
