---
name: add-harness
description: Add support for another AI coding harness (agent CLI) to loadout, end to end. Covers establishing what the harness supports, every registry, renderer, extractor, init and notice table that names a harness, tests, expected output, docs and the bundled skill. Use when asked to "add a harness", "support a new agent", "add support for (some agent CLI) to loadout", "port loadout to another harness", or "what does a new harness touch". Do NOT use to change one existing harness's renderer, or to fix a bug in one.
---

# Add a harness

On dev4 at `0756166`, a new harness touches 31 harness-keyed tables in 12 source files,
per-harness branches in 15 source files, 97 other files that name the existing harnesses, and
the expected tree. Most of these tables fail silently: a missed entry falls through to Claude's or
Pi's default, and output that looks right but isn't is the defect AGENTS.md warns about. So
work from the enumeration the script produces, not from memory, and read each match.

## Instructions

### Step 0: Ground rules

- Work in this session's own checkout. If the work belongs in another one, ask the user first.
- Never install the harness on the host, and never touch the real config directory. Run its
  binary only in a throwaway container or VM (Colima is available) with a scratch home. On the
  host, read it statically only.
- Follow AGENTS.md's "This machine is not the world". To show a capability exists, cite shipped
  docs, then the installed binary, then upstream docs; this machine only confirms. A negative
  claim carries its source inline ("verified negative: … in the 1.4.0 binary"). Stamp every fact
  with the version it was checked at.

### Step 1: Establish what the harness supports, then decide whether to add it

Write `docs/reference/<harness>.md` first, using `droid.md` and `pi.md` as models. For each
slice, record the mechanism at both scopes, or a sourced "none":

| Slice | Settle |
|---|---|
| destinations | the config-directory variable and whether it names the directory or a home (ADR 0011); global and project roots; project trust |
| instructions | the file it reads: `AGENTS.md`, `CLAUDE.md` or its own |
| permissions | the shell matcher (prefix, glob, exact; order-independent or last-match), where the catch-all lives (ADR 0016), MCP tool policy |
| mcp | the server-definition file, its keys and auth, and name rules |
| skills | its folder; whether it reads `.agents/skills`; which frontmatter it reads |
| agents | the format, the identity key, model and effort spelling |
| hooks | a native document, or registered in code (then it needs an adapter); event names; variables |
| plugins, module-config, settings | how a plugin is addressed; module files; runtime keys it writes into shared files |

Then measure the harness against ADR 0012, which dropped Antigravity because its generated file
was ignored where it mattered, it had no config-directory variable, and its mechanisms were
unestablished. If the harness fails the same tests, stop and report to the user rather than
building it. Otherwise record the adoption in a new ADR.

Success: every slice has a sourced answer and the user has agreed to which slices ship.

### Step 2: Enumerate every touch point

```bash
python3 .claude/skills/add-harness/scripts/touchpoints.py <id> --display "<Display Name>"
```

It reads the code statically and prints three lists:

- **Registries:** module-level dicts and sets naming harnesses or renderers, each marked `ok` or
  `--` for the new id.
- **Branches:** per file, the `harness == "…"` sites.
- **Files:** every file naming most harnesses.

`references/touchpoints.md` explains what each entry is for, including the ones the script
cannot see: `tests/isolation.py`, the `*_EVENTS` constants, and `HARNESS_RUNTIME_ENTRIES`.
Rerun the script after each layer. Every `--` must end up as either an entry or a decision you
can state ("Codex has no project `mcp` entry: only a stealth source writes it"). To see what
legitimate omissions look like, run it once with an existing id: `touchpoints.py pi` marks 10
registries `--`, among them `HARNESS_PREFIXES` (Pi's hooks go through an adapter) and
`CATCH_ALL_ELSEWHERE` (Pi's catch-all is in its document).

Read every branch site, especially the `if agent == … elif …` chains in `migration.py`
(`_PlanBuilder`), `discovery.py` (`live_roots`), `subagents.py` (`render_agent`) and
`unmanaged.py`. Their fall-through is a default chosen for other harnesses.

### Step 3: Implement layer by layer, test first

Work in the order of `references/touchpoints.md`: presets, then renderers, extraction, init,
notices and commands, then stealth sources and kits. For each change:

1. Write a test that fails, run it, and see it fail.
2. Make the change, and see the test pass.
3. Run a mutant on a copy, never in the live checkout: rsync to the scratchpad, apply one edit,
   grep that it landed, run the tests there.

Rules from AGENTS.md that a new harness tends to break:

- A renderer never reads files; it takes `(rules, base)` (ADR 0001).
- Key order is output: build a new dict to control position. `dedupe()` stays order-preserving.
- Reproduce the harness's quirks (ADR 0006); don't harmonise with a neighbour.
- Every renderer needs an inverse in `EXTRACTORS`/`VALUE_EXTRACTORS`, with
  `render(extract(x)) == x` (ADR 0013).
- No machine state in a generated file (ADR 0008).
- Never render a policy wider than the original's effective one. Init refuses a key whose loss
  changes behaviour, and notes one that doesn't.

Add the harness's variables to `tests/isolation.py` before any test renders to its
destinations.

### Step 4: Fixtures and expected output

1. Extend `tests/fixtures/loadout.toml`, the project fixture, `permissions.toml` and `mcp.toml`
   with shapes that provoke the new harness's quirks, and pin each in `tests/test_fixture_shapes.py`.
2. Commit the feature (`feat:`). It fails `test_project_output` and `test_permissions_output`
   by construction, because new expected files don't exist yet.
3. Run `.venv/bin/python tests/regenerate_expected.py`, read the whole diff, and commit it alone
   as `chore: regenerate expected output for <harness>` (ADR 0009). Never hand-edit
   `tests/fixtures/expected/`.

### Step 5: Docs and the bundled skill

Update every `--` file from the script's third list. `references/touchpoints.md` section 8 says
what each needs. Then check the counts: grep for the old totals ("five harnesses", "twelve
documents", the project output table) instead of assuming. Update
`src/loadout/_skills/loadout/` in the same commit as the behaviour it describes.

### Step 6: Verify every tier

```bash
timeout 900 just check
timeout 900 just test-integration
timeout 900 just test-migration
timeout 600 just build-docs
timeout 900 .venv/bin/python tests/run_init_corpus.py   # must print "converted N of N"
python3 .claude/skills/add-harness/scripts/touchpoints.py <id>   # no unexplained --
```

Read each summary line and the skip count; `test_adapters_execute.py` skips without node. If the
harness can run in a container, do one real round trip there: `init`, `sync`, then the harness
reads the files and behaves as the docs say.

Commits: `feat`, `fix` or `chore`, no scope, no body. Docs go in the same commit as the
behaviour they describe, and the expected-output regeneration goes in its own commit.

## Examples

### Example 1: a harness with a native hooks file and its own agent format

User says: "Add support for ExampleCLI to loadout."

Actions:
1. In a container, read ExampleCLI's shipped docs and binary. Write
   `docs/reference/examplecli.md`:
   - the config dir is `${EXAMPLE_HOME:-~/.example}`;
   - shell rules are glob, last-match-wins, with a catch-all in the document;
   - MCP servers live in `mcp.json`;
   - hooks live in `hooks.json`;
   - agents are YAML-frontmatter Markdown with `thinking:`;
   - verified negative, no plugins: none in the 2.3.0 binary or `docs/`.
2. ExampleCLI passes ADR 0012's tests. The user agrees, and the adoption gets an ADR.
3. `touchpoints.py examplecli` lists 31 registries, all `--`. Fill in:
   - `GLOBAL_PRESET` and `PROJECT_PRESET`;
   - `RENDERERS` and `CATCH_ALL_RENDERERS`;
   - `EXTRACTORS`;
   - `GLOBAL_ROOTS`, `PROJECT_ROOTS` and `MANAGED_PATHS`;
   - `OWNERS` and `SEEDED_CATCH_ALLS` (plus the init note);
   - `EFFORT_LEVELS` and `EFFORT_KEY`;
   - `HARNESS_PREFIXES` and an `EXAMPLECLI_EVENTS`;
   - `STARTS`;
   - `tests/isolation.py`.
4. Leave out, with a stated reason:
   - `ADDRESSED_BY`: no plugins;
   - `PREFIX_ONLY`: it has exact entries;
   - `WHOLE_FOLDER_RENDERERS`: no rules folder.
5. Commit `feat: add ExampleCLI harness support`, then the regeneration chore. Run every tier.

Result: the script shows every registry either `ok` or a stated omission, and all tiers pass.

### Example 2: "what would adding X touch?"

User asks only for scope. Run the script with X's id, summarise the counts by layer from
`references/touchpoints.md`, and list the Step 1 questions that must be answered first. Edit
nothing.

## Troubleshooting

### A test fails only on a developer machine, or writes outside the temp dir

**Cause:** the harness's config variable is missing from `tests/isolation.py`, so an exported
value relocates the destination.
**Solution:** add every relocating variable there. Never weaken the test.

### `test_project_output` fails right after the feature commit

**Cause:** expected output for the new files doesn't exist yet. That is the designed red commit.
**Solution:** run `tests/regenerate_expected.py` and commit the result alone. If existing
harnesses' expected files change too, a shared renderer or the fixture moved: explain it before
committing.

### Init adopts the harness's files into the wrong slice, or writes `settings.json` for it

**Cause:** a branch chain in `migration.py`, `discovery.py` or `subagents.py` fell through to a
default meant for another harness.
**Solution:** read the script's branch list file by file and give the harness its own branch,
with a test that would have failed on the default.

### The script reports `--` for a registry the harness genuinely lacks

**Cause:** the registry describes a capability the harness doesn't have (`ADDRESSED_BY` without
plugins, `PREFIX_ONLY` with exact matching).
**Solution:** leave it out and state why, in the reference doc and in a `test_harness_tables.py`
set equality if one covers it. An omission with a reason is a decision; one without is a defect.
