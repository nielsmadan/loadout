# 0004 — loadout renders; it does not mutate sources

**Status:** accepted (2026-08-02, milestone 1)

## Context

The system loadout replaces has both a renderer and a mutation CLI (`aiperm`) that edits the
rule source and then regenerates. The obvious move was to absorb both.

## Decision

loadout is **render-only**. It reads sources and writes generated files. It never edits a
source, and there is no `loadout allow` / `loadout deny`.

Mutation is somebody else's job: a person editing the TOML, or a separate tool.

## Consequences

- Every command is safe to run at any time. `sync` and `check` have no failure mode that
  damages a source.
- No permission rule is needed to let an agent *widen* its own permissions through loadout —
  a real hazard in the system being replaced, where the mutation CLI was allowlisted bare and
  any agent could grant itself anything.
- The source stays hand-editable, which keeps the format honest: if a rule file becomes
  awkward to write by hand, that is a signal about the format rather than something a
  generator can paper over.
- Cost: convenience UX ("allow this command") must live outside loadout. Judgement about
  *which* rule to add belongs in a skill; loadout only needs to answer whether a command
  currently matches, which is a read-only query.

## Amendment (2026-08-06, milestone 4)

`loadout init` and `loadout harness add` write configuration files, which the decision as
originally worded appears to forbid.

The safety argument was always about **rules**: there is no `loadout allow`, so no path
exists by which an agent widens its own permissions through loadout — a live hazard in the
system being replaced, where the mutation CLI was allowlisted bare and any agent could grant
itself anything. Enabling a harness changes *where* existing rules are rendered, never *what*
they permit.

Amended to read: **loadout never mutates rules; scaffolding commands may write
configuration.** Recorded as an amendment rather than a silent reinterpretation.
