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
  edit an expected file directly to make a test pass. The expected tree is **text-only**, so a
  fixture skill's supporting files must decode as UTF-8; the script refuses by name rather than
  raising a decoder error. See
  [0009](docs/decisions/0009-expected-output-is-reviewed-not-frozen.md).
  **A new artifact type therefore produces one red commit, by construction.** When a change adds
  expected files rather than rewriting them, the feature commit fails the whole-document
  comparison on its own and the regeneration that follows makes it pass — a regeneration cannot
  precede the code it renders from. `5d35f6d` + `1a6d38e` (project-scope instructions) are the
  worked example. A bisect landing on the first of such a pair is looking at this, not at a
  broken feature; the pair is always adjacent. Narrowing 0009 to "its own commit *when it
  rewrites existing output*" was considered and rejected — the case arises about once a
  milestone, and a conditional that fires that rarely is one people misremember.
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

**A precedence test must prove the loser was there and lost.** Three have been found that did
not: two ordering tests, and one named `test_a_project_deny_beats_a_template_allow` that passed
before template merging existed at all — an unmerged allow and an absent allow look identical to
an assertion about the winner.

So the check is not "would this fail if the ordering broke" — the third case survives that, since
nothing broke, the input never arrived. The check is: **assert something of the loser's that
survives.** A second rule that must still be present, a count, a position. If the only assertion
is about the winner, the test passes against a codebase where the loser is never constructed.

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

**A key whose name matches a slice is not that slice's mechanism.** Twice in one day, both from
reading `docs/reference/config.md` beside `GLOBAL_PRESET` as though one word meant the same thing
in each. `mcp` in the reference means *server definitions*; the `mcp` slice renders *tool
approval policy* — so two destinations were reported missing that were already written. OpenCode's
`instructions` key means *include files someone else wrote*; its global rules document is
`~/.config/opencode/AGENTS.md` — so a one-line preset entry was recorded as needing machinery
loadout did not have.

Neither document was wrong alone, which is what made it invisible: this is not inferring absence
from this machine, and none of the rules above would have caught it. **Before pairing a capability
document with the code's vocabulary, confirm the shared word denotes the same thing in both** —
and prefer the upstream page's own headings, which kept OpenCode's two mechanisms apart all along.

The second one cost more than effort. OpenCode's documented fallback is `~/.claude/CLAUDE.md`,
which loadout also writes, so a missing slice presented as a harness silently reading a valid
instruction document meant for a different harness. Nothing errored and nothing looked empty.

### What has actually caught these

Six wrong conclusions so far: Pi and Antigravity "have no skills" (none *installed*); OpenCode
and Pi "have no hooks" (no hooks *file* — both have documented event APIs, Pi's larger than
Claude's); "four of five harnesses have no personal-instructions tier" (grepped Claude's
`*.local.md` naming); Claude "has 16 hook events" then "31, not 30" (a seeded alternation, then a
bound); "OpenCode and Pi have no mcp destination" (they render policy inside the permissions
document); "OpenCode instructions need a two-output shape" (one preset entry, and the harness had
been reading Claude's document meanwhile).

The last two are not the same failure as the first four. Those inferred absence from this
machine; these read one word in two vocabularies. A rule aimed only at the filesystem would not
have caught them.

**Every one was caught by a second party rerunning the query — never by the author's own review.**
The rules above were written after each failure, by whoever failed. They explain the errors; they
did not catch them.

Alone, the substitute is a check that does not depend on your judgement: **run the query two ways
that should agree.** The bound sweep is the worked example — two runs, and the disagreement is
the finding.

**That rule assumes the second run happened**, and it can collapse into the first without saying
so — then the two agree because only one of them ran. A mutation whose edit never applied, a test
that **skips** rather than fails (`test_adapters_execute.py` skips without node, so a green suite
where those three skipped reads like one where they passed), a grep that matches nothing, a
`NO_RELEVANT_CONTENT` from a page that never loaded: none is distinguishable from success.

So: **a failure is self-proving** — it cannot happen unless the check ran — while a pass, a skip
and an empty match prove nothing until you confirm the check fired. "It still passed, so the test
is vacuous" is never safe; "it failed, so the test is live" always is. Grep for your own mutation
marker, read the skip count and not just the pass count, and check a pattern matches something you
know is there before trusting that it matches nothing. Neither party had this rule; the reviewing
session hit it re-running the author's mutation claim, and caught it by grepping for the marker
rather than trusting the pass.

**In `docs/reference/`, a negative claim carries its source inline** ("verified negative: no
`~/.agents/` in the 1.1.11 binary"). A bare "none" is not reviewable, and this rule fails
silently when left to memory.

## Entry points

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.
`resolve.py:_slice_root` caches resolved source roots by `Source` value. This is safe for the
one-process-per-command CLI, but a long-lived embedding must clear or bypass that cache after
replacing a source directory or symlink; otherwise a stale root can surface as a misleading
escape error.
`extract.py` runs `permissions/renderers.py` backwards — one extractor per `RENDERERS` key,
plus `merge_extractions`, which reports harness divergence rather than unioning it.
The native slices render from their own fragments rather than from rules and live beside it:
`hooks.py` (with `adapters.py`) and `plugins.py`, both registered in `RENDERERS` like everything
else, so `emit.py` composes them into a shared file without knowing what they are.
`module_config.py` is the exception that proves the shape: it has no renderer and no `RENDERERS`
entry, because a module's own config is copied byte-for-byte to an authored relative path — the
harness fixes the directory and each module picks its filename, so there is nothing to render
and nothing to derive. See [module-config](docs/reference/module-config.md).
`surgery.py` writes into a destination loadout does *not* own — `~/.codex/config.toml` — by
stripping the keys a slice declares and leaving every other byte alone. It never parses and
reserialises: comments, another tool's managed block and a multi-line string are not in the
parsed model, so the file is safe because nothing rewrites those bytes, not because a writer was
careful. `record.py` holds the other half for a slice whose key names are the user's rather than
a set loadout could enumerate: the union of what was written last time and what is written now
is what gets stripped, which is the only reason *removing* a key removes it. See
[0017](docs/decisions/0017-ownership-may-be-declared-instead-of-derived.md).
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
