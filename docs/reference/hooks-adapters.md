# Generated hook adapters

Claude, Codex and Droid read a hook document. **OpenCode and Pi register hooks in code**, so
loadout generates the code — an OpenCode plugin and a Pi extension, both rendered by
`src/loadout/adapters.py`.

This is a different mechanism, not a missing one. Neither harness lacks hooks; Pi's documented
event surface is larger than Claude's.

| harness | destination | shape |
|---|---|---|
| OpenCode | `${XDG_CONFIG_HOME:-~/.config}/opencode/plugins/loadout-hooks.js` | `export const LoadoutHooks = async ({directory}) => ({…})` |
| Pi | `${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/loadout-hooks.ts` | `export default function (pi: ExtensionAPI)` |

Auto-discovery of both directories is recorded in [config.md](config.md). Pi loads `.ts` through
jiti, so there is no build step between the generated file and execution.

## Sources

| claim | source |
|---|---|
| OpenCode's hook signatures | `@opencode-ai/plugin@1.17.0` → `dist/index.d.ts` — **typed** |
| OpenCode's `Permission` shape | `@opencode-ai/sdk@1.17.0` → `dist/gen/types.gen.d.ts:369` |
| OpenCode's plugin directory | upstream `opencode.ai/docs/plugins`, and config.md |
| Pi's events, block and mutate contracts | shipped `docs/extensions.md`, 2,988 lines |
| Claude's matcher semantics | binary 2.1.233, function reproduced below |

All four are reproducible without a credential: `npm pack @opencode-ai/plugin@1.17.0`,
`npm pack @opencode-ai/sdk@1.17.0`, and Pi's docs ship inside its npm package.

## The mapping table

The unit is *(ABI event, capability) → harness hook*. An event with no mapping on a target is
**named in a comment in the generated file and not embedded** — leaving it in the table would put
a hook in the file that looks wired and never fires. `sync` and `check` also print it as a note,
`<event> has no adapter mapping, so it will not fire` (`notices_for` in `notices.py`, whose
`_ADAPTED` set is built from these two mapping tables).

### OpenCode

| ABI event | hook | blocks | mutates |
|---|---|---|---|
| `PreToolUse` | `tool.execute.before` | throw | assign into `output.args` |
| `PostToolUse` | `tool.execute.after` | — | — |

`tool.execute.before`'s `input` is `{tool, sessionID, callID}` and carries **no arguments** —
those live only on `output.args`. So the payload's `tool_input` is read from the *output*
parameter, which is the detail most likely to be got wrong and hardest to notice.

**`permission.ask` is deliberately unused, and this corrects the ABI spec.** That spec prefers it
for denial because `output.status: "ask" | "deny" | "allow"` is a lossless three-state decision.
It is — but `Permission` is `{id, type, pattern?, sessionID, messageID, callID?, title, metadata,
time}` and carries **no tool arguments**. A `PreToolUse` hook is handed `tool_input` and decides
from it; on `permission.ask` there is nothing to hand it. The channel is lossless about the
*decision* and empty about *the input the decision is made from*.

### Pi

| ABI event | hook | blocks | mutates |
|---|---|---|---|
| `PreToolUse` | `tool_call` | `{block: true, reason}` | mutate `event.input` in place |
| `PostToolUse` | `tool_result` | — | — |
| `UserPromptSubmit` | `input` | `{action: "handled"}` | `{action: "transform", text}` |
| `SessionStart` | `session_start` | — | — |
| `SessionEnd` | `session_shutdown` | — | — |

Pi documents that **no re-validation happens after mutation**, so an adapter applying
`updatedInput` on Pi is the last check there is. Claude schema-checks `updatedInput` and rejects a
bad one; Pi will run it.

## Reachability

Per the ABI spec's Decision 4, each branch records **the named condition that would exercise it**,
not merely a claim that it is correct. A later reader can then ask whether that condition is still
*possible*, which is answerable, rather than whether the code is still *correct*, which is not.
The conditions live in `AdapterMapping.reaches` so they cannot drift from the table, and
`test_every_mapping_records_what_would_exercise_it` fails if one is dropped.

Branches known to be unreachable, and why:

| branch | unreachable on | because |
|---|---|---|
| `permissionDecision: "allow"` / `"ask"` | both | neither hook has a channel that says "allow"; reported at runtime, not dropped |
| `additionalContext` | OpenCode, and Pi's `SessionStart` | no channel; Pi's `UserPromptSubmit` does have one, via `transform` |
| a deny | `PostToolUse` on both | the tool has already run |

This matters because `pi-moves-key` was a branch that was correct, unit-tested and unreachable,
and nothing failed when it became so.

## What is not reproduced

**Claude's matcher is two branches, and only one of them is a regex.** From the 2.1.233 binary:

```js
if (!t || t === "*") return true
if (/^[a-zA-Z0-9_|]+$/.test(t))
  return t.split("|").map(trim).filter(Boolean).….includes(e)   // exact string
try { return new RegExp(t).test(e) } catch { warn(…); return false }
```

So `Bash|Edit` is an **exact-string set**, not a pattern — Claude itself warns "Hook matcher
`mcp__server` matches no tool (it is compared as an exact string)". `mcp__*` reaches the regex
branch only because `*` falls outside the simple character class, where an unanchored `.test`
happens to match. Treating every matcher as a regex would silently widen the simple ones, so
`Bash` would also guard `Bashful`.

The adapters reproduce both branches. They do **not** reproduce the two derived name sets Claude
also tests a pattern against — tool aliases and MCP name variants — because those expansions are
internal to Claude with no equivalent on either target. **A matcher relying on an alias matches on
Claude and not here.**

## Payload fidelity

Two ABI fields have no source on either harness and are **omitted rather than defaulted**:

| field | OpenCode | Pi |
|---|---|---|
| `session_id` | `input.sessionID` | — |
| `transcript_path` | — | `ctx.sessionManager.getSessionFile()` |
| `cwd` | `directory` | `ctx.cwd` |
| `permission_mode` | — | — |

A fabricated `"default"` would be worse than an absent field: a hook that relaxes its checks on a
permissive `permission_mode` would relax them on a guess. Pi's `ctx.mode` is `"tui" | "rpc" |
"json" | "print"` — a **run** mode — and mapping it here would hand a hook something that reads
like a permission posture and is not.

This answers the ABI spec's open question 3: **no**, neither harness can supply every field.

## Exit codes

Exit 2 is the ABI's block. **Every other non-zero is a script that failed**, and Claude does not
treat it as a decision — so neither does an adapter. It is reported on stderr and the call
proceeds.

This is what lets the adapter avoid the difficulty the ABI spec anticipated for OpenCode, where
throwing is the only blocking channel and "the script denied" would otherwise be indistinguishable
from "the script crashed". The protocol already separates them, so the adapter never guesses. The
alternative — fail closed on any non-zero — turns a typo in a guard into a denial of every call it
matches.

## What has no adapter

Anything using a capability outside the ABI stays **hand-written and native**, placed by the
module-config slice, whose global destination is each harness's config root
(`GLOBAL_PRESET` in `harnesses.py`). That is a permanent split, not a gap to close:

- **Pi** — `before_provider_request`, `render`, `user_bash`, `model_select`,
  `thinking_level_select`
- **OpenCode** — `shell.env`, `command.execute.before`, `chat.headers`, `tool.definition`,
  `experimental.chat.system.transform`, and the rest of the 21 documented hooks
- **Claude** — `updatedPermissions`, which rewrites the permission context rather than deciding
  one call

A `prompt`-type hook is likewise structural: an adapter spawns a command and a `prompt` hook has
none. It is filtered and **named in the generated file**, because there is no warning channel in
the render path and the file its reader would look in is that one.

## Extraction

The adapters are two of the six renderers named in `NOT_INVERTED`
(`tests/test_extract_roundtrip.py`); the other four are Codex TOML renderers, covered in
[servers.md](servers.md#codex-renderers-whose-inverse-is-not-registered). The adapters' reason is
their own. Every other renderer writes a data format, so inverting it is work someone could do.
These write JavaScript and TypeScript: a hook document is recoverable from loadout's own output
only because loadout put it there, and a plugin a user wrote by hand — the case extraction exists
for — is a program, not a document.

`test_no_renderer_lacks_an_inverse_without_being_named` fails if a renderer is added without
either an extractor or an entry.
