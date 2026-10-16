# 0029 — Coverage is measured, not inventoried

**Status:** accepted (recorded 2026-10-14 for the change made in `8a21e01` on 2026-10-11).
Supersedes the `docs/reference/coverage.md` references in
[0009](0009-expected-output-is-reviewed-not-frozen.md) and
[0013](0013-a-renderer-change-is-checked-against-extraction.md), which are left as written.

## Context

[0009](0009-expected-output-is-reviewed-not-frozen.md) made `docs/reference/coverage.md` a
hand-kept map from each documented behaviour to the test that pins it, and said a new behaviour
needs a row. [0013](0013-a-renderer-change-is-checked-against-extraction.md) cited its section on
state reachable only by calling a renderer directly, and gave extraction's rows an `x-` prefix.

`8a21e01` removed that page and added `just coverage`, every pytest test run with a branch
coverage report. Both ADRs still link to the page.

## Decision

Coverage is the report `just coverage` prints, and it is a report, not a gate. Read the two ADRs'
references to `coverage.md` through this note:

- A new behaviour needs a test that pins it; there is no row to add.
- The state that distinguishes the OpenCode and Pi renderers, unreachable through the pipeline, is
  recorded in [extraction.md](../reference/extraction.md), which also records each renderer's
  invertibility.
