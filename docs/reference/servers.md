# The mcp server definitions slice

A server definition is where it lives and how to reach it — a `url`, or a `command` and `args`.
This is distinct from the `mcp-permissions` slice, which renders *which tools may be called* from
`permissions.toml`'s `[mcp]` section. The two share a word and nothing else; conflating them has
already produced two recorded wrong conclusions
([AGENTS.md](../../AGENTS.md), "This machine is not the world"). `servers.py` never touches
`Rules`, and `mcp-permissions`'s renderers never read `mcp.toml`.

[config.md](config.md#mcp) records where each harness keeps a definition upstream, and how each
was verified. This page records what loadout does with it.

## The input is `<source>/mcp.toml`

```toml
[jina]
transport = "http"
url = "https://mcp.jina.ai/v1"
auth_env_var = "JINA_API_KEY"      # the NAME, never a token

[context7]
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
```

`transport` is `"http"` or `"stdio"`; anything else is refused at parse time, not render time —
five renderers would each fail differently, and four of them only when that harness is enabled.

- **http** needs `url`. `auth_env_var` is optional and names an environment variable, never a
  value — [0008](../decisions/0008-generated-files-carry-no-machine-state.md) forbids loadout
  stamping a secret into a generated file, so each harness applies its own interpolation at call
  time instead. `headers` is an optional table of literal values, rendered as Claude's, Droid's,
  OpenCode's and Pi's `headers` and Codex's `http_headers`. A value holding `$` or `{`, or starting
  with `!`, is refused: Claude and Pi expand `${…}`, OpenCode `{env:…}`, Pi `$env:` and a leading
  `!`, and Codex none of them, so one value would reach the harnesses as different headers. An
  `Authorization` header beside `auth_env_var`, in any case, is refused too. A literal token here is the user's
  own content in the source, reported by init like any other secret.
- **stdio** needs `command`. `args` and `env` are optional.

Every key outside `transport`, `url`, `auth_env_var`, `headers`, `command`, `args`, `env` is refused
(`SERVER_KEYS` in `servers.py`) — rejected rather than ignored, because a typo like
`auth_env_varr` would parse fine, define no auth variable, and render a server that connects
unauthenticated with nothing saying so. `plugins.py` rejects stray keys for the same reason.

A source offering no `mcp.toml` contributes no servers, the same way one offering no
`permissions.toml` contributes no tier. `mcp` is **automatic**, like `permissions` — defining a
server needs no per-agent authoring decision, so it renders without being named in a manifest's
agent block (`AUTOMATIC_SLICES` in `manifest.py`).

At project scope, a template contributes its own `mcp.toml` the same way it contributes
`permissions.toml`, `instructions.md` and `skills/` — a tier beneath the project, so a project
server of the same name replaces the template's rather than merging with it
(`project_servers` in `emit.py`; see [templates.md](templates.md)).

## Why definitions and policy stay separate

They already share a source file one level up — `[shell]` and `[mcp]` sit together in
`permissions.toml` and feed one `Rules`. What must not merge is definitions into that same
document, because the two merge under different algebras:

| | merge rule |
|---|---|
| policy (`mcp-permissions`) | union across tiers, then deny > ask > allow, order-independent ([0002](../decisions/0002-advisory-selected-enforcing-merged.md)) |
| definitions (`mcp`) | last-wins from a declared tier |

There is no strictness ordering on a URL: two tiers cannot vote on which `url` is "more correct"
the way they vote on which decision is stricter. One section holding both would need two merge
rules keyed by which field is being read — the kind of thing that reads fine when written and
produces a defect later.

## Ten destinations, nine built

Five harnesses, two scopes:

| harness | global | project |
|---|---|---|
| Claude | `${CLAUDE_CONFIG_DIR:-~}/.claude.json` → `mcpServers` | `.mcp.json` |
| Codex | `~/.codex/config.toml` → `[mcp_servers.*]` | **none for a project source** — an [overlay](#codex-project-config-applies-in-a-trusted-project) writes `.codex/config.toml` |
| Droid | `${FACTORY_HOME_OVERRIDE:-~}/.factory/mcp.json` | `.factory/mcp.json` |
| OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json` → `mcp` | `opencode.json` → `mcp` |
| Pi | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/mcp.json` | `.pi/mcp.json` |

Renderers, keyed the way `RENDERERS` in `permissions/renderers.py` names them: `claude-servers`
(global — the per-entry map wrapped in `{"mcpServers": …}`, a `MergedJsonSpec` that owns only the
`mcpServers` key of `.claude.json`), `claude-project-servers` (project — the same wrapped
document, owning `.mcp.json` outright),
`codex-servers` (TOML text; the function is scope-agnostic and no preset entry uses it now that
`codex-config` renders definitions and policy together — it stays registered for extraction),
`droid-servers` (the `{"mcpServers": …}` document at both scopes), `opencode-servers` (one
`ValueSpec` used at both scopes, contributing the `mcp` key to `opencode.json` — the same
document `permissions` also writes), `pi-servers` (`DocumentJsonSpec`,
`~/.pi/agent/mcp.json` and `.pi/mcp.json`).

### Pi's project destination is `.pi/mcp.json`

Pi 1.4.0's built-in MCP reads project servers from `.pi/mcp.json` alone, once the project is
trusted; `.mcp.json` is Claude's. `pi-mcp-adapter`, which read `.mcp.json`, is not supported in
1.0: when installed it replaces the built-in and Pi reads neither file. See
[pi.md](pi.md#server-definitions).

### Codex project config applies in a trusted project

A project `.codex/config.toml` is read by Codex only when the project is trusted, and it is a
subset of the global schema: Codex warns **"Ignored unsupported project-local config keys in
`<path>`. If you want these settings to apply, manually set them in your user-level config.toml."**
for the keys on its project-local denylist. The probe on 2026-10-30 (Codex 0.165.0, `codex exec`
and `codex mcp list` in a scratch `CODEX_HOME`, source read at `rust-v0.165.0`) answered what the
earlier note left open:

- **`mcp_servers` and `developer_instructions` in an untracked `.codex/config.toml` apply**, in a
  trusted project. Neither is on the denylist (`PROJECT_LOCAL_CONFIG_DENYLIST` in
  `codex-rs/config/src/loader/mod.rs`: `openai_base_url`, `chatgpt_base_url`,
  `apps_mcp_product_sku`, `responses_api_metadata`, `model_provider`, `model_providers`, `notify`,
  `profile`, `profiles`, two `experimental_realtime_*` URLs and `otel`). In an untrusted project the
  whole project layer is ignored, with no warning.
- **`mcp_servers` deep-merges per field.** A project server with a new name is added; a project
  server that reuses a user server's name overrides only the fields it sets and inherits the rest
  (`command`, `args`, other `env` keys). Arrays are replaced whole. A project
  `developer_instructions` replaces the user-level one, since it is a scalar.
- **Project scope still writes none.** `PROJECT_PRESET["codex"]` has no `mcp` entry, and
  `test_project_scope_renders_no_codex_config` in `tests/test_servers_wiring.py` pins that. The
  probe settled that the file applies; adding it to the project preset is a separate decision.
- **An overlay writes one**, because it can refuse the collision: it reads `~/.codex/config.toml`
  and refuses a server name the user already defined, and `check` notes an untrusted project and a
  user-level `developer_instructions` it would displace. See
  [codex.md](codex.md#overlay-destinations).

Codex's *global* destination is unaffected by this question — loadout writes `[mcp_servers.*]`
into `~/.codex/config.toml` itself, owning that one key and passing the rest of the file
through. Project scope is covered above.

Note the global renderer is `codex-config`, not `codex-servers`: definitions and approval
policy share the `[mcp_servers.<name>]` table, so one renderer emits both. `codex-servers`
remains registered because its inverse is what extraction reads back.

### Claude's global entry writes `.claude.json`

`GLOBAL_PRESET["claude"]["mcp"]` owns the top-level `mcpServers` key of
`${CLAUDE_CONFIG_DIR:-~}/.claude.json` and leaves the ~100 keys beside it untouched —
`apply_json` in `surgery.py`, the JSON sibling of the applier Codex's `config.toml` uses.

**It was staged until 2026-09-04, on two premises that were both false.** The first: that
`settings.json` has no `mcpServers` key, "so there is no file to write". True of
`settings.json`, but `.claude.json` carries that key at top level — it is where
`claude mcp add-json --scope user` writes. The evidence had been gathered from
`~/.claude.json` on a machine that sets `CLAUDE_CONFIG_DIR`, where that path is a stale
leftover; this project's own headline variable, missed in its own reference. The second:
that "render and invoke stay separate (ADR 0004)". 0004 governs loadout not editing its
rule sources and says nothing about invocation — see its amendment.

**Why parsing is safe here and forbidden for TOML.** `apply_toml` works line-wise because a
round trip loses comments, another tool's managed block and multi-line strings. JSON has none
of those, and Python preserves key order, so `json.dumps(indent=2, ensure_ascii=False)`
reproduces Claude Code 2.1.251's ~465 KB file byte for byte, bar a trailing newline. Owned
keys are assigned in place rather than popped and re-added, for the same reason `apply_toml`
replaces where content already sits: moving the key would reshuffle the file whenever the
harness rewrote it, and `check` would report that as drift on every run.

**One key, statically owned, so `record.py` is not needed.** Only Codex's `defaults` slice
(`codex-settings`) keeps a `.owned` record beside its fragment, because the keys it owns are
whichever top-level keys the user's fragment names — an open set loadout cannot enumerate. Every
other merged renderer declares a fixed set: `codex-config` owns `mcp_servers`, `codex-plugins`
owns `plugins` and `marketplaces`, and here the owned set is the constant `{"mcpServers"}`
(`RENDERERS` in `permissions/renderers.py`; `_attach_records` in `emit.py` writes a record only
for a `MergedTomlSpec` whose `owns` is a function). Writing the map is enough for add, update
*and* delete.

That last one closed a real defect. The staged path was **add-only**: `sync_claude_mcp` listed
what was registered and called `add-json` for the rest, and `claude mcp add-json` refuses to
overwrite an existing name (exits 0 saying so). Dropping a server from `mcp.toml` left it
registered, and *editing* one silently did nothing — so `mcp.toml` was authoritative for
neither deletion nor modification on the one harness this affected.

**What remains true of the objection.** That file is rewritten by the harness mid-session, so a
read-modify-write races those writes in a way `~/.codex/config.toml` does not. `check`'s
comparison is unaffected — applying is identity on an already-applied file — but a sync running
concurrently with a live session can lose a harness write. Unmeasured.

At project scope Claude reads `.mcp.json` — a file loadout owns outright, no CLI involved. The
CLI problem is global-only, which is why project scope is the easier half despite being the
motivating one.

## A server no policy names keeps each harness's default

Loadout renders no approval rule for a server `mcp.toml` defines and no `[mcp]` entry names, so
each harness applies its own default to its tools. For Pi that default is loadout's `mcp__*`
seed, `ask`. Sync and check say nothing about it: init adopts servers that had no policy before,
and a server with none is an ordinary state, not a forgotten step.

## Codex renderers whose inverse is not registered

`codex-servers` and `codex-plugins` both have a written, tested inverse — `extract_codex_servers`
and `extract_codex_plugins`, each pinned by its own test (`tests/test_extract_servers.py`,
`tests/test_extract_plugins.py`). Neither is registered in `EXTRACTORS` or `VALUE_EXTRACTORS`,
for the same reason: `codex-servers` is a `DocumentTextSpec` and `codex-plugins` a
`MergedTomlSpec`, and both produce TOML **text**, while every member of `VALUE_EXTRACTORS` takes a
**parsed document**. Registering either would make `extract_value(name, x)` mean two different
things about `x` depending on the name, and a dict passed where text is expected would fail
somewhere less obvious than the call.

`NOT_INVERTED` (`tests/test_extract_roundtrip.py`) names both, with that reason, rather than
leaving them silently missing. It also names the other two Codex `MergedTomlSpec` renderers:
`codex-config`, whose halves invert separately (definitions through `extract_codex_servers`,
policy through `codex-mcp-permissions`), and `codex-settings`, whose owned keys are known only
from the `.owned` record that extraction is never handed. Its other two entries are the hook
adapters, `opencode-hooks` and `pi-hooks` ([hooks-adapters.md](hooks-adapters.md)).

Every other definition renderer's inverse is registered in `VALUE_EXTRACTORS`:
`claude-project-servers` → `extract_claude_servers`, `claude-servers` (global) →
`extract_claude_global_servers`, `droid-servers` → `extract_droid_servers`,
`opencode-servers` → `extract_opencode_servers`, `pi-servers` →
`extract_pi_servers`.

## Reading it back

| renderer | recovers | loses |
|---|---|---|
| `claude-project-servers` (`.mcp.json`) | every field; a stray top-level key or unknown `type` is reported | — |
| `claude-servers` (global) | every field, from the `mcpServers` key of `.claude.json`; the harness's keys beside it are expected and not reported | — |
| `droid-servers` | every field from the `mcpServers` wrapper | — |
| `opencode-servers` | every field; nothing is noted about the rest of `opencode.json`, since `permission` has its own owner | — |
| `pi-servers` | every field; pi-mcp-adapter's `auth: "bearer"` with `bearerTokenEnv` as the auth variable; a stray top-level key is reported | `bearerTokenEnv` without `auth: "bearer"` and `auth: "oauth"`, both noted |
| `codex-servers` | every field, parsed from `[mcp_servers.*]` text with `tomllib` | not registered — see above |

An `Authorization` header that is exactly `Bearer ${VAR}` (Claude, Droid, Pi) or `Bearer {env:VAR}`
(OpenCode), with `VAR` one variable name, becomes `auth_env_var`. Every other header with a literal
value is carried into `headers`, from Pi's `headers` and Codex's `http_headers` too. A header
holding any other reference, such as `Bearer ${VAR:-fallback}`, refuses init with
`uncarried-setting` — extraction never invents an environment variable name from an opaque header
string, and the harness would expand the reference. An `Authorization` header beside an auth variable (Codex's `bearer_token_env_var`, or a second one
beside a Bearer reference) is not adopted, with a note: the server keeps the variable. Beside
pi-mcp-adapter's `auth: "bearer"` it refuses init with `uncarried-setting`, because the built-in
sends the header and the adapter the variable.

## Not built

- **A notice for a policy entry naming a server nothing defines.** No function reports it.

## Not verified

- **Project-scope Codex servers.** The file applies (above); whether a project source should
  write it is undecided, and project scope writes none.
