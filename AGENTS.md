# AGENTS.md

`loadout` renders one source of truth into config for four AI coding harnesses: Claude Code,
Codex, OpenCode and Pi. Antigravity (`agy`) was dropped — see
[0012](docs/decisions/0012-antigravity-is-dropped-until-it-matures.md).

## Commands

    just install      # install as an editable tool on PATH
    just check        # ruff check + ruff format --check + mypy --strict + pytest — run before every commit
    just test         # pytest only

## Constraints that silently corrupt output if violated

These are not style preferences. Each one has already caused, or nearly caused, a real defect.

- **Byte-identical output is the acceptance criterion.** Generated files must match their
  golden fixtures exactly — key order, whitespace, trailing newline. A "semantically
  equivalent" output is a failure. Content is a pure function of the source: never stamp a
  profile, commit, hash or timestamp into a generated file — see
  [0008](docs/decisions/0008-generated-files-carry-no-machine-state.md).
- **`tests/fixtures/expected/` is regenerated, never hand-edited.** Change a renderer, run
  `tests/regenerate_expected.py`, and read the diff — the diff is the review. The script
  refuses to run over unstaged changes there, so a regeneration is always its own commit. Never
  edit an expected file directly to make a test pass. See
  [0009](docs/decisions/0009-expected-output-is-reviewed-not-frozen.md).
- **The fixture's reach is load-bearing.** `tests/fixtures/permissions.toml` exists to provoke
  shapes, not to be realistic; `tests/test_fixture_shapes.py` fails if one is dropped, and
  `docs/reference/coverage.md` maps every documented behaviour to the test that pins it. When a
  test fails after a fixture change, ask whether the fixture stopped provoking the behaviour
  before you touch the assertion.
- **Key insertion order is load-bearing.** Python dicts preserve it and the output depends on
  it. Assigning into an existing dict appends; building a new one controls position. See
  `src/loadout/permissions/renderers.py:render_claude` for the case that forced this.
- **`dedupe()` must stay order-preserving and must never become `set()`.** OpenCode and Pi
  resolve last-match-wins, so emission order decides which rule applies.
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

## This machine is not the world

**The filesystem can only prove presence.** That a file exists here shows the harness reads it.
That a file is *absent* here shows only that this machine never made one — nothing whatever
about the harness. The same holds for content: `~/.claude/settings.json` lists the hook events
**you configured**, never the events Claude **supports**.

So **any claim that something does not exist needs a source that enumerates capability**. Look
in this order:

1. the harness's **shipped docs** — Pi ships 2,336 lines in `docs/extensions.md`, OpenCode and
   Claude ship none, so this is worth checking first and is often decisive
2. the **installed binary** — `strings`, bundled source maps
3. upstream documentation
4. **this machine's files — confirmatory only, and never sufficient for a negative**

The cheapest check is the one that can only mislead, which is why it is last. Reaching for it
first is the trigger to watch for in yourself.

**One person's config is not demand, either.** `~/ac` has one cross-harness skill in
forty-eight. That measures the cost of the missing mechanism, not the absence of need — a user
who found something expensive avoided it, and the avoidance is what you are counting.

**Claude is the most misleading default** — best documented, easiest to inspect, and the one
whose shape everything else gets read through. The four harnesses differ in shape, not just
naming.

### What this has already cost

Four wrong conclusions, each from reading absence off this machine:

- "Pi and Antigravity have no skills" — none were *installed*. Both have skills.
- "OpenCode and Pi have no hooks" — no hooks *file*. Both have documented event APIs, and
  **Pi's is larger than Claude's**.
- "Four of five harnesses have no personal-instructions tier" — grepping for `*.local.md`
  found only Claude's naming. All had one, in four different shapes.
- "Inline substitution serves a case that occurs once" — counted `~/ac`, which was built to
  avoid the case.

### A search seeded with the answer cannot enumerate

The rule above catches absence read off a config file. It does not catch the same error wearing
different clothes: **a query that names its own answers.** `grep -E 'Foo|Bar|Baz'` returns a
subset of what you typed — the output is your input filtered, not a finding, and it cannot
return anything you failed to think of.

**Match the shape and let the data supply the names.** Extracting `hookEventName:<fn>("…")` from
Claude's binary yields 20 events; an alternation of five guessed names yields at most five, and
looks just as much like a result.

The test, and it is quick: **if you can predict the maximum size of the result before running
the query, you are confirming, not enumerating.**

When you cannot guess the shape, use one known member to *locate* the container, then read the
container openly. Searching for `PreToolUse:` found `PreToolUse:{summary:"Before tool
execution"`, and extracting `[A-Za-z]+:{summary:"…"` then gave all 30 hook events without
naming one of them.

**A bound on the match is a seed too.** Enumerating those events with `[^"]{0,60}` returned 30,
not 31 — `SubagentStop`'s description is 64 characters, so an arbitrary limit chosen for no
reason deleted one member and manufactured a false anomaly that was nearly recorded as fact. The
query named no answers and was still shaped by a parameter the data never agreed to. Prefer `*`
to a guessed ceiling, and when a bound is unavoidable, verify the count is stable as it moves.

**Seeded queries do prove presence.** That Codex's binary contains `hookSpecificOutput` and
`permissionDecision` is established perfectly well by grepping those exact strings. What a seeded
query cannot support is **completeness** — "and nothing else". Keep the distinction or the rule
overcorrects into discarding good evidence.

Both rules are one principle: **a method that could not have surprised you cannot establish a
fact about the world.** Absence measured from your own machine, and presence measured from your
own guesses, fail the same way.

**In `docs/reference/`, a negative claim must carry its source inline** ("verified negative:
no `~/.agents/` in the 1.1.11 binary"). A bare "none" is not reviewable, and this rule fails
silently when it is left to memory.

## Entry points

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.
`machine.py` reads `$XDG_CONFIG_HOME/loadout/config.toml` — the only place machine state is
*stored*, and what `--global` resolves the root and profile from. It is not the only machine
state that is *read*: `manifest.py:resolve_destination` expands `${VAR}` in a destination
against the environment, per [0011](docs/decisions/0011-a-destination-follows-a-relocated-harness.md).
`project.py` (harness preset) is project scope; `scaffold.py` holds both scopes' scaffolding
(`init`, `harness add`, `init --global`).
`emit.py:render_project` renders it; `render_all` unions it with `render_global`'s global-scope
output, so `sync`/`check` regenerate whichever scopes are present.

## Docs

- `README.md` — commands, `loadout.toml` schema, exit codes
- `docs/scopes.md` — what loadout is for, the scope model, committed vs personal
- `docs/reference/` — per-harness matcher semantics, pattern shapes, verified quirks
- `docs/reference/coverage.md` — every documented behaviour and the test that pins it
- `docs/decisions/` — ADRs; append-only, superseded rather than edited

When behaviour changes, update the doc that covers it in the same commit.
