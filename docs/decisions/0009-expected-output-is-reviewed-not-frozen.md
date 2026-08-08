# 0009 — Expected output is reviewed, not frozen

**Status:** accepted (2026-08-08, milestone 5). Supersedes the fixture-freeze half of
[0003](0003-port-byte-identical-before-changing-behaviour.md).

## Context

[0003](0003-port-byte-identical-before-changing-behaviour.md) captured the live system's output
as frozen fixtures and made byte-identical reproduction the acceptance criterion. A mismatch
meant the port was wrong, never that the fixture was stale. That was correct, and it worked:
every renderer was proven against the system it replaced.

The port is now over. `permissions/sync.py` is deleted, `permissions/manage.py` is superseded,
and as of milestone 5 `~/ac` stages no generated files at all. There is no original left to be
faithful to, so the fixtures no longer measure fidelity to anything — they measure fidelity to
their own past.

Two costs had become visible. The fixtures were a snapshot of one person's real configuration,
which is the wrong thing for a tool other people are meant to use, and which had already
drifted from its origin. And the freeze taxed legitimate changes: swapping a hand-written TOML
emitter for a library changed `[mcp_servers."jina"]` to `[mcp_servers.jina]` — the same document
by `tomllib`, differing only in quoting TOML does not require — and that one line could not land
without ceremony disproportionate to it.

The freeze was also carrying weight it was never designed for. An audit found that all 55 tests
in `tests/test_permissions_renderers.py` already construct `Rules(...)` from inline synthetic
entries, so every matcher and ordering quirk — including the Pi-moves-a-key versus
OpenCode-leaves-it-in-place pair from [0006](0006-faithful-ports-reproduce-upstream-quirks.md) —
is pinned independently of any fixture. What whole-document comparison uniquely covers is
wiring: that a target reaches the renderer it names, that a base is applied, that `preserve`
carries a foreign key through, that a profile selects. Nothing about matcher semantics.

## Decision

Expected output is **regenerated deliberately and protected by review**, not by prohibition.

- The source is `tests/fixtures/`, synthetic and built to provoke shapes rather than to be
  anyone's configuration.
- `tests/regenerate_expected.py` is the only sanctioned way to update
  `tests/fixtures/expected/`. It refuses to run when that tree has unstaged changes, so a
  regeneration is always its own reviewable commit and can never ride along inside a feature
  diff.
- `tests/test_fixture_shapes.py` asserts the fixture still carries every shape the comparison
  depends on. A trimmed fixture would otherwise keep passing while covering less.
- `docs/reference/coverage.md` maps each documented behaviour to the test that pins it. A
  behaviour with no test is a gap; a new behaviour needs a row.

What 0003 established and this does **not** change: byte-identical output remains the
acceptance criterion, content stays a pure function of the source
([0008](0008-generated-files-carry-no-machine-state.md)), and a behaviour change still ships as
its own reviewed commit.

## Consequences

- A rendering change now lands as: change the renderer, run the regeneration script, read the
  diff. The diff is the review, and it is visible in the commit rather than hidden behind a
  passing test.
- The fixtures no longer encode anyone's real commands, service names or prose, so the suite is
  meaningful to someone who did not write `~/ac`.
- Coverage improved rather than degraded in the swap. The project-scope fixture previously held
  only two-word `just` prefixes — no glob, no MCP entry, no bare command, and an empty personal
  tier that made the two-tier merge the identity function. Its replacement exercises all of
  those, and `test_missing_personal_tier_is_not_an_error` now proves the tier contributes
  instead of passing vacuously.
- `test_base_drift_guard` was dropped. It asserted that `~/ac`'s two Claude base documents
  differ only by `CLAUDE_AFK_TIMEOUT_MS` — a property of that repository's content, not of
  loadout. If it is still wanted, it belongs in `~/ac`, where the real bases live.
- The risk this accepts: nothing now structurally prevents someone regenerating to make a
  failing test pass. The mitigations are procedural — the script's own refusal to run over
  unstaged changes, and the shape test that fails when the fixture loses reach.
