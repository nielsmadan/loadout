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
  fixtures in `tests/fixtures/expected/` exactly — key order, whitespace, trailing newline. A
  "semantically equivalent" output is a failure. Content is a pure function of the source:
  never stamp a profile, commit, hash or timestamp into a generated file — see
  [0008](docs/decisions/0008-generated-files-carry-no-machine-state.md).
- **`tests/fixtures/expected/` is regenerated, never hand-edited.** Change a renderer, run
  `tests/regenerate_expected.py`, and read the diff — the diff is most of the review, but not
  all of it for a renderer change: whether a file can still be read back into rules is invisible
  in expected output, and is pinned by `tests/test_extract_*.py` instead — see
  [0013](docs/decisions/0013-a-renderer-change-is-checked-against-extraction.md). The script
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

**The filesystem proves presence, never absence.** A file existing here shows the harness reads
it; a file missing here shows only that this machine never made one. Same for content:
`~/.claude/settings.json` lists the hook events *you configured*, never the ones Claude
*supports*.

So a claim that something does not exist needs a source that **enumerates capability**, in this
order — **shipped docs** (Pi ships 2,336 lines in `docs/extensions.md`; it beat every grep),
**the installed binary**, upstream docs, then this machine, which is confirmatory only. The
cheapest check is the one that can only mislead, so reaching for it first is the trigger to
notice in yourself.

**A search seeded with the answer cannot enumerate.** `grep -E 'Foo|Bar|Baz'` returns your input
filtered. Match the *shape* and let the data supply the names. Test: **if you can predict the
result's maximum size before running the query, you are confirming, not enumerating.** When the
shape is unknown, use one known member to *locate* the container, then read the container openly
— seeding to locate is fine, seeding to enumerate is the error.

**A bound is a seed too.** `[^"]{0,60}` returned 30 of 31 hook events because one description ran
64 characters. The query named nothing and was still shaped by a number the data never agreed to.
Prefer `*`; when a bound is unavoidable, **rerun at two bounds and check the count is stable.**

**Seeded queries do prove presence** — that Codex's binary contains `hookSpecificOutput` is
established fine by grepping it. Only **completeness** is beyond them. Keep that distinction or
the rule overcorrects into discarding good evidence.

**One person's config is not demand.** `~/ac` has one cross-harness skill in forty-eight; that
measures the cost of a missing mechanism, not the absence of need.

**Claude is the most misleading default** — best documented, easiest to inspect, and the lens
everything else gets read through.

### What has actually caught these

Four wrong conclusions so far: Pi and Antigravity "have no skills" (none *installed*); OpenCode
and Pi "have no hooks" (no hooks *file* — both have documented event APIs, Pi's larger than
Claude's); "four of five harnesses have no personal-instructions tier" (grepped Claude's
`*.local.md` naming); Claude "has 16 hook events" then "31, not 30" (a seeded alternation, then a
bound).

**Every one was caught by a second party rerunning the query — never by the author's own review.**
The rules above were written after each failure, by whoever failed. They explain the errors; they
did not catch them.

Alone, the substitute is a check that does not depend on your judgement: **run the query two ways
that should agree.** The bound sweep is the worked example — two runs, and the disagreement is
the finding.

**In `docs/reference/`, a negative claim carries its source inline** ("verified negative: no
`~/.agents/` in the 1.1.11 binary"). A bare "none" is not reviewable, and this rule fails
silently when left to memory.

## Entry points

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.
`extract.py` runs `permissions/renderers.py` backwards — one extractor per `RENDERERS` key,
plus `merge_extractions`, which reports harness divergence rather than unioning it.
The native slices render from their own fragments rather than from rules and live beside it:
`hooks.py` (with `adapters.py`) and `plugins.py`, both registered in `RENDERERS` like everything
else, so `emit.py` composes them into a shared file without knowing what they are.
`machine.py` reads `$XDG_CONFIG_HOME/loadout/config.toml` — the only place machine state is
*stored*, and what `--global` resolves the root and profile from. It is not the only machine
state that is *read*: `manifest.py:resolve_destination` expands `${VAR}` in a destination
against the environment, per [0011](docs/decisions/0011-a-destination-follows-a-relocated-harness.md).
`project.py` (harness preset) is project scope; `scaffold.py` holds both scopes' scaffolding
(`init`, `harness add`, `init --global`).
`templates.py` resolves a template *name* — the project's vendored copy first, then the
`templates/` directory of every source the machine's global manifest declares — and owns the tree
hash `template sync` compares a vendored copy against. A template is a source, so it merges
through the slice's own operator and adds no merge rule.
`emit.py:render_project` renders it; `render_all` unions it with `render_global`'s global-scope
output, so `sync`/`check` regenerate whichever scopes are present.

## Docs

- `README.md` — commands, `loadout.toml` schema, exit codes
- `docs/scopes.md` — what loadout is for, the scope model, committed vs personal
- `docs/reference/` — per-harness matcher semantics, pattern shapes, verified quirks
- `docs/reference/extraction.md` — the renderers run backwards: what round-trips, what is reported
- `docs/reference/templates.md` — declared vs vendored, name resolution, the content hash, sync
- `docs/reference/coverage.md` — every documented behaviour and the test that pins it
- `docs/decisions/` — ADRs; append-only, superseded rather than edited

When behaviour changes, update the doc that covers it in the same commit.
