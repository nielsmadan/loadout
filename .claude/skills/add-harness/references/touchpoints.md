# Where a harness touches loadout

Surveyed on dev4 at `0756166`, plus dev2 at `bc83227` (read only) for stealth sources and kits.
Symbols are stable across checkouts; line numbers are not, so find each by name. Run
`scripts/touchpoints.py NAME` for the live list and treat this file as the explanation of it.

## 1. The two presets: what loadout writes, and where

| File | Symbol | Add |
|---|---|---|
| `src/loadout/harnesses.py` | `GLOBAL_PRESET` | one table: a `SliceOutput` per slice the harness supports, with a `destination` template carrying its config-directory variable (`${VAR:-~/.dir}`). `KNOWN_HARNESSES` and the CLI's `--harness` choices derive from it |
| `src/loadout/harnesses.py` | `SHARED_SKILL_READERS` | the id, only if it reads `.agents/skills` at both scopes |
| `src/loadout/harnesses.py` | `DIRECTORY_SLICES`, `DROID_AGENTS_DIRECTORY` | a constant of its own if its agents folder is not `agents/` |
| `src/loadout/project.py` | `PROJECT_PRESET` | the project table, `output=` paths relative to the repo root. `preserve_foreign=True` for a file the harness also keeps its own keys in. A missing slice gets a one-line comment saying why (see `codex` and `mcp`) |
| `src/loadout/manifest.py` | `AUTOMATIC_SLICES`, `_agent_slices` | usually nothing. Branches on `codex` for `settings`/`preserve`; check whether the new harness needs the same refusal |

`SliceOutput` fields are documented on the class: `takes_settings`, `owned_key`, `preserve`,
`source_slice`. Choose them from the harness's real file shape, not by copying a neighbour.

## 2. Renderers: what each slice's bytes are

| File | Symbol | Add |
|---|---|---|
| `src/loadout/permissions/renderers.py` | `RENDERERS` | one entry per renderer name used in either preset. Spec type decides ownership: `JsonSpec`, `TextSpec`, `ValueSpec` (one key), `ValuesSpec(owns=…)`, `DocumentJsonSpec`/`DocumentTextSpec` (whole file), `MergedJsonSpec`/`MergedTomlSpec` (named keys of a file the harness shares) |
| same | `CATCH_ALL_RENDERERS` | the renderer, if its document carries a shell catch-all (ADR 0016) |
| same | `MCP_NAMESPACES`, `MCP_KEYS`, `MCP_NAMESPACE_HARNESS` | if MCP tool policy is written as keys in the permission document |
| same | `PROJECT_CATEGORIES` and the `*-project` renderers | a project renderer only if key order or content differs from global (the comment above `PROJECT_PRESET` says why these exist) |
| `src/loadout/servers.py` | `render_<h>_servers`, `__all__` | the server-definition renderer; name rules like `PI_SERVER_NAME` if the harness rejects some names |
| `src/loadout/hooks.py` | `<H>_EVENTS`, `HARNESS_PREFIXES`, `render_<h>_hooks` | for a native hooks document: its event names and its own variable namespace |
| `src/loadout/adapters.py` | `<H>_MAPPINGS`, `_<H>_SUBSCRIPTIONS`, `render_<h>_adapter` | instead of the above, for a harness that registers hooks in code (OpenCode, Pi). `notices._ADAPTED` derives from the mappings |
| `src/loadout/plugins.py` | `ADDRESSED_BY`, `render_<h>_plugins` | how a plugin reference is addressed: `marketplace` or `source` |
| `src/loadout/subagents.py` | `EFFORT_LEVELS`, `EFFORT_KEY`, `EFFORT_SPELLING`, `EXTENSION`, `render_agent` | the agent file format; `render_agent` branches per harness and its fallthrough writes Claude-style Markdown frontmatter |
| `src/loadout/skills.py` | `CLAUDE_ONLY_KEYS`, harness branches | skill frontmatter the harness does or does not read |
| `src/loadout/emit.py` | renderer and agent branches | `CLAUDE_IMPORT`, the `.agents/skills` sharing, rule-less permission files (`renderer in {...}` near the empty-render check) |

## 3. Extraction: rendering backwards (ADR 0013)

| File | Symbol | Add |
|---|---|---|
| `src/loadout/extract.py` | `EXTRACTORS`, `VALUE_EXTRACTORS` | an inverse for every renderer, or a name in the exemption that `test_no_renderer_lacks_an_inverse_without_being_named` reads |
| same | `<H>_SERVER_KEYS`, `SUPPORTED_UNCARRIED_TYPES`, document-key sets like `DROID_MCP_DOCUMENT_KEYS` | which keys round-trip, which are noted, which refuse init (the Q1 rule: refuse a dropped key whose loss changes behaviour, note one that doesn't) |

## 4. Init: discovery and migration

| File | Symbol | Add |
|---|---|---|
| `src/loadout/discovery.py` | `GLOBAL_ROOTS` | `(VARIABLE, fallback)`; `live_roots` has per-harness branches for a variable that names a home rather than the directory (Droid) or needs a suffix (OpenCode) |
| same | `PROJECT_ROOTS` | the project config folder |
| same | `MANAGED_PATHS["global"]`, `MANAGED_PATHS["project"]` | the allowlist init reads (ADR 0024). Anything not listed is left alone unread |
| same | `RUNTIME_DOCUMENTS`, `RUNTIME_KEYS` | a document the harness also writes runtime state into, and those keys |
| same | `DESTINATION_ENVIRONMENT` | derives from `GLOBAL_ROOTS`; add any extra variable the destinations depend on |
| same | `AGENTS_MD_READERS` | derives from `PROJECT_PRESET`; check the result |
| `src/loadout/migration.py` | `OWNERS` | the top-level keys each slice owns in a shared document |
| same | `RESIDUAL_SLICE`, `SEEDED_CATCH_ALLS` | if the residual is not `settings`; if the first render seeds a catch-all (then add the init note) |
| same | `_PlanBuilder` adoption chain (`if agent == "claude" … elif agent == "pi"`) | the dormant destinations init plans for the harness. **The chain's defaults (`settings.json`, `AGENTS.md`, JSON) are where a new harness lands silently** |
| `src/loadout/runtime_paths.py` | `HARNESS_RUNTIME_ENTRIES` | entries the harness writes into folders loadout also writes |
| `src/loadout/unmanaged.py` | `WHOLE_FOLDER_RENDERERS`, `ROOT_FILES_IGNORED`, skill-folder branches | how the harness reads a folder whole, and what in it is a skill |
| `src/loadout/native_documents.py` | parsers | a document format loadout does not yet read (JSONC, YAML, …) |

## 5. Sync-time notices and commands

| File | Symbol | Add |
|---|---|---|
| `src/loadout/notices.py` | `CATCH_ALL_ELSEWHERE`, `PREFIX_ONLY`, `known_events`, `unreached_catch_all` | where the catch-all lives if not in the document, naming the settings slice that sets it; whether its matcher has an exact form; the hook events it knows |
| `src/loadout/skill_copy.py` | `STARTS` | how a user starts the bundled skill in that harness |
| `src/loadout/cli.py`, `init_workflow.py`, `completions.py` | | derive from `KNOWN_HARNESSES`; check the help and prompt text still reads right |

## 6. Stealth sources and kits (dev2 onward; formerly overlays and templates)

| File | Symbol | Add |
|---|---|---|
| `src/loadout/stealth_render.py` | `STEALTH_PRESET` | what a stealth source writes into a repo that has not adopted loadout |
| `src/loadout/stealth_targets.py` | `_HOME_TEMPLATES` | the global config directory template |
| `src/loadout/stealth_check.py` | `SKILL_FOLDERS` | the project skills folder, if any |
| `src/loadout/permissions/rules.py` | `_SECTIONS` | harness-named sections of `permissions.toml`, if the harness gets one |
| `src/loadout/_kits/`, `kit_catalog.py` | | bundled kits, if they carry harness-specific parts |

## 7. Tests

| File | Add |
|---|---|
| `tests/isolation.py` `HARNESS_VARIABLES` | **every variable that relocates the harness.** `conftest.fake_home` clears these; one left out lets a developer's exported variable send test writes to a real config |
| `tests/test_harness_tables.py` | runs on its own once the presets change. Add the new harness's tables to `COMPLETE` or to a set equality, and add a test for any new restated table |
| `tests/fixtures/loadout.toml` | `[permissions.<renderer>]` entries and a `[<h>]` agent block |
| `tests/fixtures/project/`, `permissions.toml`, `mcp.toml` | shapes that provoke the harness's quirks; `tests/test_fixture_shapes.py` pins that a shape stays provoked |
| `tests/fixtures/expected/` | regenerated by `tests/regenerate_expected.py`, never edited, in its own `chore` commit after the feature commit (ADR 0009). The feature commit is red against `test_project_output`/`test_permissions_output` by construction |
| `tests/test_extract_roundtrip.py`, `test_extract_fixtures.py`, `test_extract_*` | `render(extract(x)) == x` for each renderer, and the shipped artifacts |
| `tests/test_project_output.py`, `test_permissions_renderers.py`, `test_servers_wiring.py`, `test_hooks_wiring.py`, `test_plugins_wiring.py`, `test_agents_render.py` | per-slice behaviour |
| `tests/test_adapters_execute.py` | executes code adapters under node; it **skips** without node, so read the skip count |
| `tests/fixtures/init_corpus.json`, `tests/run_init_corpus.py` | a real-repo case using the harness, if one exists; the run must print `converted N of N` |
| `tests/test_loadout_skill.py` | pins the bundled skill's harness facts |

## 8. Documentation

| File | Add |
|---|---|
| `docs/reference/<harness>.md` | new: destinations, each slice's mechanism and matcher semantics, version-stamped evidence, quoted upstream lines, verified negatives with their source inline. Model: `droid.md`, `pi.md` |
| `docs/reference/README.md` | the cross-cutting rules (order, globs, exact entries, catch-all, relocating the config directory) and the upstream documentation table |
| `docs/reference/config.md` | the per-category column |
| `docs/reference/servers.md`, `hooks-adapters.md`, `plugins.md`, `models.md`, `module-config.md`, `extraction.md`, `migration.md`, `sync.md`, `architecture.md` | the harness's row or section in each |
| `docs/decisions/` | an ADR for adopting it, measured against 0012's reasons for dropping Antigravity. ADRs are append-only, so don't edit old ones |
| `docs/user/reference/harnesses.md` | both scope tables, plus setup notes |
| `docs/user/reference/cli.md`, `reference/configuration.md`, `guides/*.md`, `troubleshooting.md`, `index.md`, `getting-started.md` | harness lists, the "What sync generates" table, the project output count |
| `docs/scopes.md` | the project-document count and per-harness notes |
| `README.md`, `AGENTS.md` (first paragraph), `mkdocs.yml` (`site_description`) | the harness list |
| `src/loadout/_skills/loadout/` | `SKILL.md`, `references/configuration.md`, `references/onboarding.md`. Agents follow these literally |
