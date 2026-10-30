# OpenCode

## Config files

Configuration files are **merged together, not replaced**, across these locations:

| location | |
|---|---|
| remote | `.well-known/opencode` endpoint |
| global | `~/.config/opencode/opencode.json` |
| custom | path in `OPENCODE_CONFIG` |
| project | `opencode.json` in project root |
| inline | `OPENCODE_CONFIG_CONTENT` |
| managed | `/Library/Application Support/opencode/` |

There is no import or include directive, and no way to split keys within one location.
A separate permissions file is possible only by using a *different location* — e.g.
`OPENCODE_CONFIG` pointing at a generated file — at the cost of an env var on every
invocation.

The global location is `(XDG_CONFIG_HOME ?? ~/.config) / "opencode"`, so **`XDG_CONFIG_HOME`
is the variable that moves the file loadout writes** — not `OPENCODE_CONFIG_DIR`, which adds
a further `.opencode`-shaped directory that shadows the global config rather than replacing
it. Verified 2026-08-09 against opencode 1.18.15's `globalConfigPath`. See
[Relocating the config directory](README.md#relocating-the-config-directory).

## Resolution

**Last matching rule wins.** Emission order is load-bearing: denies must be emitted after
the allows they refine. The renderer emits `allow`, then `deny`, then `ask`, over a map
seeded with the catch-all as its first entry: `{"*": <[shell] default>}`, and `ask` when the
source states none. Being first is what lets every rule refine it — see
[The catch-all default](README.md#the-catch-all-default).

## Pattern shape

`permission.bash` is a map of pattern → decision:

```json
"bash": { "*": "ask", "pwd": "allow", "pwd *": "allow", "git push": "deny" }
```

**A plain prefix is emitted in both forms**, `<entry>` and `<entry> *`. Since v1.1.15, `pwd *`
also matches a bare `pwd`, so on current OpenCode the bare form is redundant and harmless; before
that it was what made the rule cover the bare command. The current source
(`packages/core/src/util/wildcard.ts` in
[anomalyco/opencode](https://github.com/anomalyco/opencode) at `4acdeffbe`, the file's latest
commit; the same line is in v1.18.33):

```ts
  if (escaped.endsWith(" .*")) escaped = escaped.slice(0, -3) + "( .*)?"
```

That arrived in `62702fbd11` (*"fix: permissions wildcarding so that for ex: 'ls *' includes ls *
AND 'ls' to prevent having to double state commands or use 'ls*'"*, 2026-01-12), then in
`packages/opencode/src/util/wildcard.ts`. The first release containing it is v1.1.15; v1.1.14
does not. Before it, `*` compiled to `.*` inside `^…$`, so `pwd *` required an argument. This is
established from upstream history through the GitHub API, not from an installed binary: the
sandboxed session that checked it could not read one.

Entries ending in `*` are kept literal; OpenCode's matcher handles the glob. An exact entry
(`=pwd`) is emitted as the bare form **only**, which is what makes it exact here.

MCP entries become top-level keys under `permission`, not nested:

```json
"permission": { "jina_*": "allow" }
```

from a source entry of `jina/*` — the `/` becomes `_`.

## What loadout emits

From `GLOBAL_PRESET["opencode"]` in `harnesses.py` and `PROJECT_PRESET["opencode"]` in
`project.py`. Global paths are under `${XDG_CONFIG_HOME:-~/.config}/opencode`.

| slice | global | project |
|---|---|---|
| permissions | the `permission` key of `opencode.json` (`render_opencode`) | the `permission` key of `opencode.json` at the repository root |
| MCP server definitions | the `mcp` key of `opencode.json` (`render_opencode_servers`) | the `mcp` key of the same `opencode.json` |
| hooks | `plugins/loadout-hooks.js`, a generated plugin ([hooks-adapters.md](hooks-adapters.md)) | not in the project preset |
| agents | `agents/`, each written with `mode: subagent` unless the source sets a `mode` | `.opencode/agents/` |
| skills | `skills/` | `.opencode/skills/`, or the shared `.agents/skills/` when Codex is also declared and the copies are identical |
| instructions | `AGENTS.md` | `AGENTS.md` at the repository root, shared with Codex, Droid and Pi |
| module config | authored paths beneath the root, such as hand-written plugin files in `plugins/` | not in the project preset |

**`opencode.json` has two owners**, the `permissions` and `mcp` slices, each writing its own key.
Everything else in the file (`$schema`, `model`, `provider`) comes from the `[opencode] settings`
fragment globally, and from the existing file at project scope (`preserve_foreign`). OpenCode has
no plugins slice: a plugin is on because its file is in `plugins/`, so there is no enablement
list to render.

`[opencode.extra]` in `permissions.toml` supplies additional `permission.<key>` toggles
verbatim — currently `skill`, `webfetch`, `websearch`, `codesearch`.

`AGENTS.md` is the global instructions document. It is a document at a path, like the
other four harnesses — **not** the `instructions` key in `opencode.json`, which is a separate
include feature for rule files someone else already wrote (globs and remote URLs among them).
Loadout leaves that key untouched, so nothing is included twice. Upstream calls the document
"global rules … applied across all opencode sessions".

**Until this destination existed, OpenCode fell back to `~/.claude/CLAUDE.md`** — which loadout
also writes. OpenCode was reading Claude's document, with Claude's fragments in it, and the
fallback is silent, so nothing looked broken.

## Gotcha: v2 renames nearly every key

OpenCode v2 renames most configuration keys, including
`mcp.{server}.enabled` → `mcp.servers.{server}.disabled` — a **silent boolean
inversion**. A config that appears to migrate cleanly will have every server's
enabled state flipped.

## Gotcha: compound bash commands take the least-permitted verdict

OpenCode evaluates each part of a `;` / `&&` / `|` chain separately and takes the
least-permitted result. In a headless session there is no TTY to answer an `ask` prompt,
so the whole call is auto-rejected and the run terminates before producing output. The
classic trigger is a benign `echo ---` separator inside an otherwise-allowed read chain.

## Required setup: `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`

**Set this before letting loadout write OpenCode skills, or OpenCode picks between
two versions of every skill at random.**

    export OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1

OpenCode scans six locations for skills — its own two, Claude's two, and the
`.agents/` pair — and loadout writes to Claude's *and* OpenCode's, deliberately,
because a skill's content can differ per harness (`::: opencode` sections). That
means every skill name exists twice, and the two copies are not always the same
bytes.

**Duplicate names resolve by race, not by precedence.** From
`packages/opencode/src/skill/index.ts`:

```ts
yield* Effect.forEach(discovered.matches, (match) => add(state, match, events), {
  concurrency: "unbounded",
  discard: true,
})
```

Every discovered `SKILL.md` is read concurrently, and `add` writes into one
name-keyed object — so whichever file finishes last wins. `add` does log
`"duplicate skill name"` at warning level, which a normal session never shows.

Verified by probing rather than inferred: the same repo, the same two copies, run
twice, returned `CLAUDE_PROJECT` and then `OPENCODE_PROJECT`. The documented order
of the six locations describes *discovery*, not resolution, and upstream never
claims otherwise — the word "precedence" appears nowhere on its skills page.

The variable removes both Claude directories from the scan, so nothing collides
and OpenCode reads the copy written for it. It is read from the **environment**
(`ConfigProvider.fromEnv()`); there is no `opencode.json` key for it, so it belongs
in your shell configuration rather than anywhere loadout renders.

`OPENCODE_DISABLE_CLAUDE_CODE=1` sets it too, along with the Claude Code prompt —
the flag is `broad || direct`, so anything *checking* whether the collision is
disabled has to read both names or it reports a false alarm at whoever set the
broad one.

`OPENCODE_DISABLE_EXTERNAL_SKILLS=1` removes `.agents` from OpenCode's scan.
Loadout writes Codex project skills there. No extra flag is needed when the
Codex and OpenCode variants are byte-identical, because Loadout emits one shared
copy. When their variants differ, Loadout emits `.agents/skills` for Codex and
`.opencode/skills` for OpenCode; `loadout check` reports the resulting duplicate
unless this flag is set or the skill's `agents` policy selects only one of them.

**Why the race and not an ordering.** The duplicate branch warns and does not
return, so the assignment after it runs anyway:

```ts
if (state.skills[md.data.name]) { yield* Effect.logWarning("duplicate skill name", …) }
state.skills[md.data.name] = { … }        // runs unconditionally
```

A `return` there would make it first-wins, which — with a fixed discovery order —
would be a precedence rule. Without one it is whichever concurrent read lands
last. "Warn on duplicate" reads like a guard and is not one.
