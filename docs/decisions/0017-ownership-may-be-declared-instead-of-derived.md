# 0017 — Ownership may be declared instead of derived

**Status:** accepted (2026-08-28, codex delivery)

## Context

[0001](0001-render-never-reads-its-own-output.md) split a config file's two roles: a **base
document** holds the hand-maintained content in the repo and is an input, and the renderer
writes the generated keys on top. Nothing generated is ever read back, so nothing can feed
forward.

That works whenever the foreign content can live in the repo. Two cases where it cannot:

**`~/.codex/config.toml` has no committable base.** It carries 32 `[projects."…"]` tables
Codex writes as projects are opened, a `# >>> nono:… >>>` block another tool manages, comments,
and a `developer_instructions = """…"""` string. That is machine state; no file in the repo
could hold it. So Codex gets no base — and therefore no loadout-managed settings at all.
`model_reasoning_effort` was hand-typed into the live file and would not survive a reinstall.
loadout's answer so far was to render a *staged* file into the source repo and stop, leaving
delivery to a script outside loadout. `docs/reference/codex.md` points the reader at
`~/ac/codex/sync_config.py` — a path in one machine's private repo, which is not a mechanism
anyone else can install.

**The base does not scale to a multi-purpose file.** `loadout/settings/claude.json` is 212
lines and 17 top-level keys. loadout generates one of them, `permissions`. The other sixteen —
`theme`, `statusLine`, `cleanupPeriodDays`, `awaySummaryEnabled` — are in loadout's source
repo solely so that a render does not delete them.

The base is doing two unrelated jobs: **preventing deletion**, and **version-controlling
settings**. The second is worth having on its own. The first is what drags all 212 lines in
whether or not anyone wanted them versioned.

### Why not simply overwrite the keys loadout owns

Because ownership derived from the current source cannot express a removal. Remove the mcp
slice: no renderer emits `mcpServers`, so nothing overwrites it, and the key survives every
later run. It is then indistinguishable from hand-maintained content — the next render
preserves it as foreign — so the error is permanent and self-reinforcing. This is 0001's
"corruption propagates forward", reached from the other direction.

## Decision

A slice may **declare** the keys it owns, independently of what the current source produces.
Where it does, rendering reads the destination, removes every declared key, and writes the
current values into what remains. A base is then optional rather than required.

0001's invariant is unchanged: everything loadout generates is regenerated unconditionally on
every render, and only content loadout never generates survives. Declared ownership reaches it
by a different route — 0001 strips generated keys **in the repo**, this strips them **at the
destination**.

Four constraints make it equivalent rather than weaker:

1. **Declared, never derived.** The owned set must not be computed from the current source, or
   deletion breaks again exactly as above. Always emitting a key — `mcpServers: {}` for no
   servers — is an equivalent spelling, and is preferred where the slice still exists.
2. **One pass per file.** Where several slices target one document, compose every owned key
   first and apply once. Applying per slice means the second slice reads the first's output and
   rebuilds the feedback 0001 exists to prevent. `emit.py` already groups contributors by path
   before composing; this extends that grouping to the write.
3. **Text, where the parse is lossy.** For `config.toml` the strip is line-wise surgery, not
   parse-and-serialise: comments, the managed block and the multiline string are not in the
   parsed model, so a round trip destroys the content the strip exists to protect. The file is
   safe because nothing rewrites those bytes — not because a writer was careful.
4. **Renderers stay pure.** The caller reads the destination and hands it in, as
   `preserve_foreign` already does. A renderer still takes its inputs and returns a document.

## Consequences

- Codex `mcp`, `plugins` and `settings` get real destinations. The merge step becomes loadout
  machinery instead of a script each user has to write; the dead reference in
  `docs/reference/codex.md` goes with it.
- A base becomes optional and additive. Keys worth having on a fresh machine stay in the repo;
  the rest stay on the machine. The choice is per key rather than all-or-nothing.
- Cost: **strip-and-write cannot reconstruct a file it never carried.** A key dropped from a
  base stops arriving on a new machine. That is a real loss, now chosen deliberately rather
  than paid by default.
- Cost: the declared list is a second place ownership is written down, and can drift from what
  a renderer actually emits. A test must pin that the two agree, or a key silently stops being
  stripped while still being written.
- 0001's vacuous-golden-test hazard returns wherever an expected fixture's foreign content came
  from the file being compared. Fixtures supply that content deliberately, and the assertions
  bite on the owned subtree.
- A destination that does not exist yet renders from empty, so a clean machine still
  reproduces. What it cannot reproduce is content that was never loadout's.
