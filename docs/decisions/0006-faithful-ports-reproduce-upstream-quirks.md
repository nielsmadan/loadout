# 0006 — A faithful port reproduces upstream quirks, including ones that look like bugs

**Status:** accepted (2026-08-03, milestone 3)

## Context

The seven renderers were ported from one file where several were near-copies of each other.
Some of their differences look like inconsistencies a tidy-minded reader would "fix".

The sharpest example: Pi and OpenCode are both last-match-wins and both emit two pattern forms
per entry, so their renderers look nearly identical. But `render_pi` deletes a key before
reassigning it, so an overwritten key **moves to the end of the map**, while `render_opencode`
assigns in place, so the key **stays where it was**. Both are faithful to their originals.

A reviewer applied the harmonisation — adding Pi's `pop` to `render_opencode` — and **all 164
tests passed**, goldens included. It passed because no entry in the live rule file appears in
two categories, so the reordering never fires on real data.

## Decision

Where the ported original differs between harnesses, reproduce the difference exactly. Do not
harmonise for consistency. Where a difference is genuinely a defect, fix it as a deliberate,
separately-documented change after the port is proven — never inside it (see
[0003](0003-port-byte-identical-before-changing-behaviour.md)).

Any behaviour that only a specific input combination reveals must be pinned by a test that has
been **watched to fail** when the behaviour is removed. A test never seen failing is not
evidence.

## Consequences

- Renderers carry comments saying *why* an apparent inconsistency is correct, at the exact
  line where a maintainer would otherwise be tempted.
- Some faithfully-reproduced quirks are real defects awaiting a deliberate fix: the local-scope
  Pi renderer emits only `<entry> *` and never the bare form, so a granted command still
  prompts when invoked with no arguments.
- Golden tests alone do not protect these differences — they only exercise one real rule set.
  Behaviour-specific tests with proven discrimination are the protection.
