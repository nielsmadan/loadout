# Pi

## Config file

`${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/pi-permission-system/config.json` globally and
`.pi/extensions/pi-permission-system/config.json` in a project, consumed by the
`@gotgenes/pi-permission-system` extension — not by Pi itself. The global document pins its
schema in the emitted `$schema` key:

```
https://cdn.jsdelivr.net/npm/@gotgenes/pi-permission-system@23.0.0/schemas/permissions.schema.json
```

Pi is one of two harnesses whose permissions already live in their own file, so
`loadout` fully owns this output and it needs no base document. The pinned version is a
literal in the renderer — bumping the extension means bumping it there.

`PI_CODING_AGENT_DIR` replaces `~/.pi/agent`, and `extensions/` is joined onto it, so this
file moves with it. Verified 2026-08-09 against pi 0.84.1. See
[Relocating the config directory](README.md#relocating-the-config-directory).

Modules keep their own configuration beside this file — Pi offers no config API, so each
picks its own path. See [module-config.md](module-config.md).

## What loadout emits

From `GLOBAL_PRESET["pi"]` in `harnesses.py` and `PROJECT_PRESET["pi"]` in `project.py`. Global
paths are under `${PI_CODING_AGENT_DIR:-~/.pi/agent}`.

| slice | global | project |
|---|---|---|
| permissions | `extensions/pi-permission-system/config.json` (`render_pi`) | `.pi/extensions/pi-permission-system/config.json` (`render_pi_project`) |
| hooks | `extensions/loadout-hooks.ts`, a generated extension (`render_pi_adapter`) | not in the project preset |
| MCP server definitions | `mcp.json`, pi-mcp-adapter's file (`render_pi_servers`) | not in the project preset; pi-mcp-adapter reads the project's `.mcp.json`, which the Claude project preset writes when Claude is declared |
| plugins | the `packages` key of `settings.json` (`render_pi_plugins`), with a `[pi] settings` fragment as the rest of the document and `lastChangelogVersion` carried from the live file | not in the project preset |
| agents | `agents/`, read by the `pi-subagents` package, not by Pi core | `.pi/agents/` |
| skills | `skills/` | `.pi/skills/`, or the shared `.agents/skills/` when Codex is also declared and the copies are identical |
| instructions | `AGENTS.md` | `AGENTS.md` at the repository root, shared with Codex, Droid and OpenCode |
| module config | authored paths beneath the root | not in the project preset |

A `[pi] settings` fragment with no `plugins` renders nothing, and sync and check print a note
saying so: `settings.json` is written by the plugins slice, so declare `plugins` under `[pi]`, even
as an empty list.

## Resolution

**Last matching rule wins**; the package docs say "put broad catch-alls first."

Emission order is load-bearing, and more so than on OpenCode: when a later category
writes a pattern that an earlier one already set, the renderer **deletes the key and
reinserts it** so it moves to the end of the map. Overwriting in place would leave the
key at its original position, where an intervening rule could still win.

Order is `allow`, `ask`, `deny` — deny last.

## Document shape

```json
{
  "$schema": "...",
  "permission": {
    "*": "allow",
    "bash": { "*": "ask", ... },
    "mcp":  { "*": "ask", ... }
  }
}
```

Both `bash` and `mcp` maps are seeded with a catch-all as their first entry. `bash` takes
`[shell] default`, and `ask` when the source states none; `mcp` is always `ask` — the key is
a shell key, and MCP policy has its own three lists. See
[The catch-all default](README.md#the-catch-all-default).

`render_pi_project` writes the catch-all only when the project source states a default. Pi merges
the project config over the global one, shallow-merging each pattern map
(`mergeFlatPermissions` in pi-permission-system's `src/policy/permission-merge.ts`:
`{ ...baseVal, ...value }`), and resolves a map last-match-wins (`patterns.findLast` in
`src/policy/wildcard-matcher.ts`). So a project `*` replaces the global `*`'s verdict in its
first position, and a project that states nothing keeps the global catch-all. With no rule and no
default, the project file is not written at all.

## Pattern shape

Pi matches with its own wildcard regex, not minimatch: `*` compiles to `.*`, `?` to one
character, and the last matching pattern wins. Since v5.12.0 a pattern ending in ` *` also matches
the bare command. The current source says so (`src/policy/wildcard-matcher.ts` in
[gotgenes/pi-packages](https://github.com/gotgenes/pi-packages) at `c8f89b5`, package version
40.2.0):

```ts
  // If the pattern ends with " *" (space + wildcard), make the trailing
  // space-and-arguments portion optional so that e.g. "git *" matches both
  // "git status" and bare "git". Mirrors OpenCode wildcard semantics.
  if (escaped.endsWith(" .*")) {
    escaped = `${escaped.slice(0, -3)}( .*)?`;
  }
```

That arrived in `c25b0b5` (*"feat: support trailing wildcard optionality (#123)"*, 2026-05-08),
first released in v5.12.0 of the standalone repo, gotgenes/pi-permission-system. Through v5.11.2 a
trailing ` *` required arguments, so `foo *` did not match a bare `foo`. Verified negative: no
`minimatch` in `package.json` at any of the standalone repo's 83 tags (v0.1.4 to v5.18.1), nor in
the current monorepo package; the monorepo's own releases in between were not checked.

Loadout still emits a plain prefix in both forms, `<entry>` and `<entry> *`. On v5.12.0 and later
the bare form is redundant and harmless; on earlier versions it is what makes the rule cover the
bare command. Entries ending in `*` are kept literal. An exact entry (`=pwd`) is emitted as the
bare form only, at both scopes.

### MCP targets are derived, not `server/tool`

Pi never sees the `server/tool` form. It derives the target from the call's own input:

| call | target |
|---|---|
| tool call | `<server>_<tool>` and `<server>:<tool>` |
| list / search | `mcp_server_<server>` |
| connect | `mcp_connect_<server>` |

The last two are emitted **only for a server-wide entry** (`server/*`). A rule scoped to
a single tool therefore cannot decide whether the whole server may be listed — that is
deliberate, not an oversight.
