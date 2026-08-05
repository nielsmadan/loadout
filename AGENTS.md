# AGENTS.md

`loadout` renders one source of truth into config for five AI coding harnesses: Claude Code,
Codex, Antigravity (`agy`), OpenCode and Pi.

## Commands

    just install      # install as an editable tool on PATH
    just check        # ruff check + ruff format --check + mypy --strict + pytest — run before every commit
    just test         # pytest only

## Constraints that silently corrupt output if violated

These are not style preferences. Each one has already caused, or nearly caused, a real defect.

- **Byte-identical output is the acceptance criterion.** Generated files must match their
  golden fixtures exactly — key order, whitespace, trailing newline. A "semantically
  equivalent" output is a failure.
- **`tests/golden/expected/` is frozen truth**, captured from the live system it replaced. If
  a golden comparison fails, **the code is wrong**. Never edit a golden to make a test pass.
  Changing one is a deliberate, separately-reviewed act — see
  [0003](docs/decisions/0003-port-byte-identical-before-changing-behaviour.md).
- **Key insertion order is load-bearing.** Python dicts preserve it and the output depends on
  it. Assigning into an existing dict appends; building a new one controls position. See
  `src/loadout/permissions/renderers.py:render_claude` for the case that forced this.
- **`dedupe()` must stay order-preserving and must never become `set()`.** OpenCode and Pi
  resolve last-match-wins, so emission order decides which rule applies.
- **Zero third-party runtime dependencies.** `tomllib` is stdlib and read-only. There is no
  TOML writer — `codex/mcp-permissions.toml` is emitted as hand-built text on purpose.
- **Renderers never read files.** A renderer takes `(rules, base)` and returns a document.
  The base is a parameter, never a read of the renderer's own prior output — see
  [0001](docs/decisions/0001-render-never-reads-its-own-output.md).

## Fidelity over consistency

The renderers are ports. Where two harnesses look almost identical but behave differently,
that difference is deliberate and must be preserved. The known trap: `render_pi` deletes a key
before reassigning it so the key **moves** to the end of the map; `render_opencode` assigns in
place so the key **stays put**. Harmonising them looks like a tidy-up and silently breaks
output. See [0006](docs/decisions/0006-faithful-ports-reproduce-upstream-quirks.md).

Tests that assert values often fail to pin *order*. Before trusting a test named after an
ordering property, check it would actually fail if the ordering broke — two have been found
that would not.

## Never generalise from Claude Code

loadout targets five harnesses whose mechanisms differ in shape, not just naming. Claude is
the best-documented and easiest to inspect, which makes it a misleading default. Establish
each harness's behaviour separately and record it in `docs/harnesses/`. Absence of a
Claude-style filename in another harness proves nothing.

## Entry points

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.

## Docs

- `README.md` — commands, `loadout.toml` schema, exit codes
- `docs/scopes.md` — what loadout is for, the scope model, committed vs personal
- `docs/harnesses/` — per-harness matcher semantics, pattern shapes, verified quirks
- `docs/decisions/` — ADRs; append-only, superseded rather than edited

When behaviour changes, update the doc that covers it in the same commit.
