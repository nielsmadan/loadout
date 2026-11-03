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

`settings.json` is written by the plugins slice, so a `[pi] settings` fragment with no `plugins`
is refused as a manifest error, exit 3: "pi: `settings` renders only through the plugins slice;
declare `plugins` under [pi], even as an empty list". As a note, it let the next sync retire the
whole file, `defaultModel` included, once `plugins` was removed.

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

There is no top-level `"*"`, the extension's universal fallback, so every tool no surface names,
such as `read`, `write` and `edit`, resolves to the extension's default, `ask`
(`src/permission-manager.ts` at the pinned 23.0.0):

```ts
  /** Universal fallback when permission["*"] is absent from all scopes. */
  const DEFAULT_UNIVERSAL_FALLBACK: PermissionState = "ask";
```

Loadout 0.9.3 and earlier wrote `"*": "allow"`, which let them all run without asking.

`render_pi_project` writes the catch-all only when the project source states a default, and no
`mcp__*`. Pi merges
the project config over the global one, shallow-merging each pattern map
(`mergeFlatPermissions` in pi-permission-system's `src/policy/permission-merge.ts`:
`{ ...baseVal, ...value }`), and resolves a map last-match-wins (`patterns.findLast` in
`src/policy/wildcard-matcher.ts`). So a project `*` replaces the global `*`'s verdict in its
first position, and a project that states nothing keeps the global catch-all. Neither file has a
top-level `"*"`, so a project inherits none from loadout's global file either. With no rule and no
default, the project file is not written at all.

## What init reads from a Pi config

Loadout 1.0 carries only Pi's shell and MCP rules: `permission.bash`, the `mcp__*` tool keys, and
pi-mcp-adapter's `mcp` map. Init sorts every other key of an original by whether losing it changes
what Pi allows (`_pi_settings` and `_pi_surfaces` in `src/loadout/extract.py`). It refuses those
that do as `uncarried-setting`, in one issue naming each key and the file, and notes the rest.
The settings are the ones `schemas/permissions.schema.json` lists at the pinned 23.0.0, and its
`additionalProperties: false` is enforced (`strictObject` in `src/config-schema.ts`), so the
extension rejects a file holding any other key.

| key | global | project |
|---|---|---|
| a top-level `"*"` of `ask` | noted: the extension's own fallback | refused |
| any other top-level `"*"`, and every other surface, such as `read`, `write`, `edit`, `path`, `skill` or `external_directory` | refused | refused |
| `yoloMode`, `piInfrastructureReadPaths`, `authorizerChain`, `shellTools` at the value the extension applies without them (`false`, `[]`, `[]`, `{}`) | noted | refused |
| the same four at any other value | refused | refused |
| `debugLog`, `permissionReviewLog`, `doublePressToConfirm`, `toolInputPreviewMaxLength`, `toolTextSummaryMaxLength` | noted | noted |
| a key the schema does not list, such as the legacy `defaultPolicy` | refused | refused |

A project value replaces the global one, so at project scope even a default value is not
inert: a project `"yoloMode": false` switches off a global `true`. `mergeUnifiedConfigs` in
`src/config-loader.ts`, for the scalars:

```ts
    const value = override[key] ?? base[key];
```

and `mergeFlatPermissions` in `src/permission-merge.ts` does the same for a string surface such as
`"*"`: `merged[key] = value;`.

Why each setting falls where it does, from the source at the pinned tag:

- `yoloMode` rewrites every `ask` to `allow` (`src/permission-manager.ts`, whose comment says
  "only the resolution path sees the ask→allow rewrite"): `? rewriteAsksToYolo(composedWithSession)`
- `piInfrastructureReadPaths` adds directories whose reads skip the `external_directory` gate
  (`src/permission-session.ts`): `...(this.config.piInfrastructureReadPaths ?? []),`
- `authorizerChain` names the authority links consulted before the terminal authorizer
  (`src/index.ts`): `getAuthorizerChain: () => configStore.current().authorizerChain ?? [],`
- `shellTools` routes another tool through the bash rules (`src/access-intent/tool-kind.ts`):
  `const alias = aliases?.[name];`
- `debugLog` and `permissionReviewLog` only switch log files (`src/logging.ts`):
  `if (!options.getConfig().debugLog) {` and `if (!options.getConfig().permissionReviewLog) {`
- `doublePressToConfirm` asks for a second key press in the prompt
  (`src/authority/permission-prompt-decision.ts`):
  `if (!config.doublePressToConfirm || state.armedKey === key) {`
- `toolInputPreviewMaxLength` and `toolTextSummaryMaxLength` truncate the prompt's preview
  (`src/tool-preview-formatter.ts`):
  `config.toolInputPreviewMaxLength ?? TOOL_INPUT_PREVIEW_MAX_LENGTH,`

The extension's shipped `config/config.example.json` is refused at either scope: it holds
`shellTools`, `path`, `read`, `write`, `edit`, `skill` and `external_directory`, and in a project
also `yoloMode`, `piInfrastructureReadPaths`, `authorizerChain` and `"*"`. The same run also
refuses it as `uncarried-rule` for three rules it cannot read: the deny with a reason
`"npm *": { "action": "deny", "reason": "Use pnpm instead" }`, and `mcp_status` and `mcp_list` in
the `mcp` map, which name no server ([extraction.md](extraction.md)).

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

A server name may itself hold `_`, of which the separator `__` is made, so a rule for one server
can reach another's tools: `a/*` is `mcp__a__*`, which also matches `mcp__a__b__read`, a tool of
server `a__b`, and `mcp__a___read`, a tool of server `a_`.
Render refuses such a rule, whatever its decision, naming both servers, against every server the
same source names in a rule or in `mcp.toml`. **A server the source does not name cannot be
checked**: one added through `/mcp`, or, for a project, one declared only at global scope. A
rule can still reach its tools.

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
