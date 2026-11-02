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
| MCP server definitions | `mcp.json`, the file Pi's built-in MCP reads (`render_pi_servers`) | `.pi/mcp.json`, which Pi reads once the project is trusted |
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
    "mcp__*": "ask",
    "mcp__docs__search": "allow",
    ...
  }
}
```

`bash` is seeded with a catch-all as its first entry, taking `[shell] default`, and `ask` when the
source states none. MCP rules are top-level keys after it, seeded with `mcp__*`, which is always
`ask`: `[shell] default` is a shell key, and MCP policy has its own three lists. See
[The catch-all default](README.md#the-catch-all-default).

`render_pi_project` writes the catch-all only when the project source states a default, and no
`mcp__*`. Pi merges
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

### MCP targets are tool names

Pi 1.4.0's built-in MCP registers each tool as `mcp__<server>__<tool>`, with every character
outside `[A-Za-z0-9_]` replaced by `_` (`createMcpToolName` in `dist/extensions/mcp/tools.js`).
pi-permission-system classifies such a tool as an extension tool and matches its name against the
top-level keys of `permission`, so `docs/search` renders as `"mcp__docs__search"` and `my-svc/*` as
`"mcp__my_svc__*"`. `*` and `?` are kept, being wildcards there.

Pi gives a tool a hash suffix when its sanitised name collides with another tool's or runs past
64 characters, and no rule can predict the suffix. So `render_pi` and `render_pi_project` refuse
two servers whose names sanitise alike, two targets that become one key, and a target without a
wildcard whose key is longer than 64 characters.

pi-permission-system's `mcp` map governs only pi-mcp-adapter's single `mcp` proxy tool. loadout
does not render it: pi-mcp-adapter is not supported in 1.0, because when installed it replaces
the built-in MCP, and Pi then does not read `mcp.json` at all. Init reads an `mcp` map it finds and
carries its rules as tool names, with a note.

### Server definitions

`render_pi_servers` writes the `mcpServers` shape Pi's built-in MCP validates
(`validateMcpServerConfig` in `dist/core/mcp-servers.js`). An auth variable renders as an
`Authorization: Bearer ${VAR}` header, which Pi expands; `bearerTokenEnv` is pi-mcp-adapter's key
and the built-in ignores it (verified negative: no `bearerTokenEnv` in the 1.4.0 package). A
server name outside `^[A-Za-z0-9_-]+$`, which Pi rejects, is refused, and so are two names that
differ only in `-` and `_`, since Pi rejects the second.

`/mcp` saves enable and exposure changes into the file that defines the server, and "Disable in
this project" adds an override entry to `.pi/mcp.json`. Both files are written whole, so either
change is drift.
