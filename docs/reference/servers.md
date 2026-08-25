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
four renderers would each fail differently, and three of them only when that harness is enabled.

- **http** needs `url`. `auth_env_var` is optional and names an environment variable, never a
  value — [0008](../decisions/0008-generated-files-carry-no-machine-state.md) forbids a secret
  reaching a generated file, so each harness applies its own interpolation at call time instead.
- **stdio** needs `command`. `args` and `env` are optional.

Every key outside `transport`, `url`, `auth_env_var`, `command`, `args`, `env` is refused
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

## Eight destinations, six built

Four harnesses, two scopes:

| harness | global | project |
|---|---|---|
| Claude | staged `claude/mcp-servers.generated.json` | `.mcp.json` |
| Codex | staged `codex/config.toml` (`[mcp_servers.*]` only) | **none — open question** |
| OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json` → `mcp` | `opencode.json` → `mcp` |
| Pi | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/mcp.json` | **none — `.mcp.json` already serves it** |

Renderers, keyed the way `RENDERERS` in `permissions/renderers.py` names them: `claude-servers`
(global — a flat `{name: entry}` map, staged), `claude-project-servers` (project — the same
per-entry shape wrapped in `{"mcpServers": …}`, owning `.mcp.json` outright),
`codex-servers` (TOML text, staged — the function is scope-agnostic but only the global preset
entry is wired), `opencode-servers` (one `ValueSpec` used at both scopes, contributing the `mcp`
key to `opencode.json` — the same document `permissions` also writes), `pi-servers` (`DocumentJsonSpec`,
global only).

### Pi has no project destination

`pi-mcp-adapter`'s shipped README calls `.mcp.json` the **"Preferred project config"** and says Pi
**"uses it immediately."** Writing `.pi/mcp.json` as well would hand Pi the same servers twice
under two names. loadout already writes `.mcp.json` for Claude at project scope, and Pi reads it
without any help from loadout — one file serving several harnesses, the same shape project
instructions already use, where Codex, OpenCode and Pi share one `AGENTS.md`.

### Codex has no project destination yet

Whether `[mcp_servers.*]` survives Codex's project-config filter is unverified. Codex's own
warning: **"Ignored unsupported project-local config keys in `<path>`. If you want these settings
to apply, manually set them in your user-level config.toml."** So a project `.codex/config.toml`
is a *subset* of the global schema, and some keys are silently dropped with that warning — whether
`mcp_servers` is one of them has not been checked.

The probe that would settle it: write a scratch `.codex/config.toml` containing
`[mcp_servers.probe]`, trust the directory, run Codex, and see whether the ignored-keys warning
names `mcp_servers`. That needs an authenticated Codex, so it stays open rather than guessed at —
an absent entry is honest, whereas one written on an assumption is a failure this project has
recorded six times ([AGENTS.md](../../AGENTS.md), "This machine is not the world").
`test_codex_gets_no_project_destination_yet` in `tests/test_servers_wiring.py` names the day to
delete it: when the probe answers.

Codex's *global* destination is unaffected by this question — `~/ac/mcp/sync.py` already writes
`[mcp_servers.*]` into `~/.codex/config.toml` today, and loadout's `codex-servers` renderer
reproduces that byte-for-byte. The open question is project scope only.

### Claude's global entry is staged, not written

`${CLAUDE_CONFIG_DIR:-~}/.claude.json` is runtime state — history, project entries, caches — and
`settings.json` has no `mcpServers` key at all. There is no file loadout can render into. So
`GLOBAL_PRESET["claude"]["mcp"]` sets `output` and no `destination` — loadout renders
`claude/mcp-servers.generated.json` and stops; something else feeds it to `claude mcp add-json`,
exactly as `mcp/sync.py` does today. Render and invoke stay separate
([0004](../decisions/0004-loadout-is-render-only.md)) rather than loadout learning to shell out to
a harness's own CLI — this reproduces a split that already existed in the system being replaced,
rather than introducing one.

At project scope Claude reads `.mcp.json` — a file loadout owns outright, no CLI involved. The
CLI problem is global-only, which is why project scope is the easier half despite being the
motivating one.

## A server defined but not permitted is reported

`unpermitted_servers` (`notices.py`) names every server `mcp.toml` defines that no `[mcp]` policy
entry — allow, ask or deny — mentions by name, in declaration order. Two files, two edits: define
the server, then allow its tools. Forgetting the second gives a server whose tools are all denied
and no error anywhere. The notice is silent as soon as a policy names the server at all, even for
one tool — it flags a definition nobody has said anything about, not policy completeness.

```
$ loadout check
mcp.servers: jina is defined but no [mcp] policy allow/ask/deny entry names it — its tools are all denied with no error
```

Advisory only, like every notice — it never moves the exit code.

## Two renderers whose inverse cannot be registered

`codex-servers` and `codex-plugins` both have a written, tested inverse — `extract_codex_servers`
and its `codex-plugins` counterpart, each pinned by its own test
(`tests/test_extract_servers.py`, `tests/test_extract_plugins.py`). Neither is registered in
`EXTRACTORS` or `VALUE_EXTRACTORS`, for the same reason: both are `DocumentTextSpec` renderers
producing TOML **text**, while every member of `VALUE_EXTRACTORS` takes a **parsed document**.
Registering either would make `extract_value(name, x)` mean two different things about `x`
depending on the name, and a dict passed where text is expected would fail somewhere less obvious
than the call. So both stay named in `NOT_INVERTED` (`tests/test_extract_roundtrip.py`) with that
reason, rather than being silently missing.

Every other definition renderer's inverse is registered in `VALUE_EXTRACTORS`:
`claude-project-servers` → `extract_claude_servers`, `claude-servers` (global) →
`extract_claude_global_servers`, `opencode-servers` → `extract_opencode_servers`, `pi-servers` →
`extract_pi_servers`.

## Reading it back

| renderer | recovers | loses |
|---|---|---|
| `claude-project-servers` (`.mcp.json`) | every field; a stray top-level key or unknown `type` is reported | — |
| `claude-servers` (staged global) | every field, from the flat `{name: entry}` map with no `mcpServers` wrapper | — |
| `opencode-servers` | every field; nothing is noted about the rest of `opencode.json`, since `permission` has its own owner | — |
| `pi-servers` | every field, including `bearerTokenEnv`; a stray top-level key is reported | — |
| `codex-servers` | every field, parsed from `[mcp_servers.*]` text with `tomllib` | not registered — see above |

A `headers.Authorization` value that is not a plain `Bearer ${VAR}` (Claude) or
`Bearer {env:VAR}` (OpenCode) is reported rather than guessed at — extraction never invents an
environment variable name from an opaque header string.

## Not built

- **The reverse notice.** The design this slice argues from
  ([2026-08-22-mcp-definitions-slice-design.md](../superpowers/specs/2026-08-22-mcp-definitions-slice-design.md#6-three-failure-modes-all-reported-none-fatal))
  describes reporting in both directions — a policy entry naming a server nothing defines, as well
  as a server nothing permits. Only the second direction is built (`unpermitted_servers` above);
  the first has no function and no test.

## Not verified

- **Whether `[mcp_servers.*]` survives Codex's project-config filter** — the open question above.
  It blocks nothing: project scope already serves Claude, Pi (through `.mcp.json`) and OpenCode,
  and Codex's project destination is the fourth, added when the probe answers.
