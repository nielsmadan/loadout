# The module-config slice

A plugin is enablement; a **module's own configuration** is a third thing, owned by
neither the plugins slice nor settings. This slice carries it — verbatim, at the path
the module itself reads.

## Why the path is authored, never derived

**Pi provides no config API.** `ExtensionAPI` (0.84.1, `dist/core/extensions/types.d.ts`)
has 25 members and none of them is a config accessor — verified negative, enumerated from
the interface body rather than by grepping for the word, which returns only
`config: ProviderConfig` inside `ExtensionRuntimeState.pendingProviderRegistrations`, an
unrelated type. The package root exports path helpers alone (`dist/index.d.ts:2` →
`CONFIG_DIR_NAME`, `getAgentDir`), and the only shipped guidance is
`docs/extensions.md:949-971`: compose your own path, gate on `ctx.isProjectTrusted()`.

So the harness fixes the *directory* and each module picks its own filename. Two shapes
result, and both are in use:

| shape | modules | evidence |
|---|---|---|
| `<agent-dir>/<pkg>.json` | `@narumitw/pi-statusline`, `@narumitw/pi-plan-mode`, `@narumitw/pi-lsp` | each package's README and `src/settings.ts` |
| `<agent-dir>/extensions/<dir>/config.json` | `pi-subagents`, `pi-permission-system` | `pi-subagents/src/extension/config.ts:11-12` |

The second is the load-bearing one. `pi-subagents` reads
`join(getAgentDir(), "extensions", "subagent", "config.json")` — directory `subagent`,
package `pi-subagents`. `pi-lsp` reads a legacy `lsp.json`. **A destination derived from a
module's name is wrong for both**, so the relative path inside the source tree is the
declaration, exactly as a directory is for skills.

Verified negative, sourced: no per-package key in Pi's settings vocabulary —
`docs/settings.md` "All Settings" enumerates every key and none is module config. The
object form of a `packages` entry filters *which resources load* (`docs/packages.md`
§Package Filtering), not how a module behaves.

## What loadout does

`<source>/module-config/<agent>/<relative path>` is copied to
`<agent config dir>/<relative path>`. Nothing is parsed, merged or serialised.

The destination in `GLOBAL_PRESET` is a **root** rather than a file — the only slice
where that is true — and `module-config = false` switches it off, since the directory
being the declaration means an absent key already means *automatic*.

**Bytes are copied, not rendered**, for a reason that shows up immediately: the live
`pi-statusline.json` on the machine this was written against is tab-indented by the
module's own writer, while loadout's JSON writer emits two spaces. Rendering would
rewrite every line of a file loadout has no schema for, every sync. `Copied` also
preserves the executable bit, so module config that is a script rather than a document
survives.

Two collisions are refused rather than resolved: two sources offering the same relative
path (ambiguous in the same way two sources offering one skill name is), and a
module-config path landing on a destination another slice renders — `pi/settings.json`
would otherwise race the plugins slice.

## What this slice does not do

**Project scope.** Pi documents `.pi/<name>.json` for trusted projects
(`docs/extensions.md:961`, and pi-lsp reads one). No instance exists on the machine this
was surveyed against, so it is not built — see
[scopes.md](../scopes.md#still-open).

**Machine state.** `Copied` is verbatim, so loadout cannot strip state and the gate is
authorial, as it already is for a skill's supporting files. The same Pi agent directory
holds `mcp-cache.json`, `run-history.jsonl`, `trust.json`, `auth.json` and
`mcp-onboarding.json` — module and harness state, none of it trackable without breaking
[0008](../decisions/0008-generated-files-carry-no-machine-state.md).

**Orphan removal.** Deleting a source file leaves the destination in place, the problem
0008 defers for every slice. Removal is manual until the sidecar lands.

## The module writes the same file

`/statusline` and `pi-subagents`' `updateConfig` save to the paths this slice owns
(`pi-statusline/src/settings.ts:360-401`, `pi-subagents/src/extension/config.ts:31-34`).
Under loadout ownership an edit made in the module's own UI reads as drift and is
reverted by the next sync. That is not new — the same already applies to Pi's
`settings.json`, which `/settings` edits — but it makes the workflow explicit: **edit in
the module's UI, then copy the bytes back into the source.** `loadout check` is what
surfaces the difference.

## Other harnesses

Pi is the only agent with a `module-config` entry today. The mechanism is harness-neutral
and each addition is one preset line, but neither candidate has demand yet: Claude's
`~/.claude/hooks/` scripts are still symlinked by `~/ac/sync.sh`, and OpenCode has no
module config to speak of, because there a plugin's `.ts` file *is* its enablement
(see [plugins.md](plugins.md)).
