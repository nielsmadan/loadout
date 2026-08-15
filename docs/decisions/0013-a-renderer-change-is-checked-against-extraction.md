# 0013 — A renderer change is checked against extraction, not only against expected output

**Status:** accepted (2026-08-14). Extends
[0006](0006-faithful-ports-reproduce-upstream-quirks.md) with a second, independent basis; does
not supersede it.

## Context

[0006](0006-faithful-ports-reproduce-upstream-quirks.md) forbids harmonising renderers that look
alike but behave differently, on grounds of **output fidelity**: the ported original differs, so
the port must differ. Its worked example is `render_pi` deleting a key before reassigning it —
so an overwritten key moves to the end of the map — against `render_opencode` assigning in
place, so the key stays put.

That ADR also records why the prohibition is hard to hold: a reviewer applied the harmonisation
and all 164 tests passed, because no entry in the live rule file appears in two categories. The
distinguishing state is unreachable through the pipeline —
[coverage](../reference/coverage.md#reachable-only-by-calling-a-renderer-directly) records the
same for `pi-moves-key`, since `merge_rules` resolves deny > ask > allow before any renderer
runs.

Extraction (`src/loadout/extract.py`) changes what that difference costs. Reading a rendered
artifact back into `Rules` is only possible where rendering was injective, and the two
behaviours are not equally invertible:

- `render_pi`'s key-move leaves the map **grouped by decision**, so which category each rule came
  from is still readable. The file round-trips.
- `render_opencode` and `render_pi_project` assign in place, so a rule listed in two categories
  keeps its **first** category's position while carrying its **last** category's value. No source
  ordering renders back to that, so the file does not round-trip and extraction must report it.

The reachability argument does not transfer. A file loadout wrote cannot contain the ambiguous
shape, but **extraction's input is not loadout's output** — it is a machine's hand-maintained
configuration, which is the entire reason the command exists. There, the shape is ordinary.

## Decision

Harmonising two renderers that differ now breaks two things, not one: output fidelity (0006) and
the ability to read that harness's files back. Both must be weighed, and the second is not
visible in `tests/fixtures/expected/`.

Therefore a change to any renderer is checked against the round-trip properties in
`tests/test_extract_*.py` as well as against regenerated expected output. A green
`regenerate_expected.py --check` is no longer sufficient evidence that a renderer change is safe.

Where a renderer is genuinely made *more* invertible, that is a behaviour change and follows
[0003](0003-port-byte-identical-before-changing-behaviour.md): separately committed, with the
diff reviewed, never folded into other work.

## Consequences

- The invertibility of each renderer is recorded in
  [extraction.md](../reference/extraction.md), and its rows in
  [coverage.md](../reference/coverage.md) carry the `x-` prefix.
- 0006's "watched to fail" rule extends to extraction: the pattern-collapse property was
  confirmed by removing the collapse and observing the failure. That mutation is worth repeating
  by hand when the collapse is touched — it produces a *different source* that renders
  *identical bytes*, so the document round trip cannot catch it and only the rules round trip
  can.
- Two known losses are accepted rather than fixed, because fixing them would change output:
  `render_codex` files glob entries under an uncategorised comment block, and
  `render_codex_project` runs entries through `shlex.split`. Both are reported by extraction; see
  [extraction.md](../reference/extraction.md#what-each-renderer-loses).
