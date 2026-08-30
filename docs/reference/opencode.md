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

**A plain prefix must be emitted in both forms** — `<entry>` and `<entry> *` — because
`pwd *` does not match a bare `pwd`. Entries ending in `*` are kept literal; OpenCode's
matcher handles the glob.

MCP entries become top-level keys under `permission`, not nested:

```json
"permission": { "jina_*": "allow" }
```

from a source entry of `jina/*` — the `/` becomes `_`.

## What loadout emits

`opencode/opencode.json` → the `permission` key only.

The rest of the file (`$schema`, `model`, `provider`) is hand-maintained, so this is a
base-document case like Claude's.

**This file has two owners.** `permissions/sync.py` writes `permission`;
`mcp/sync.py` writes `mcp`. Neither may clobber the other's key. This is why the real
output count across the repo is 12 distinct paths, not 13.

`[opencode.extra]` in `permissions.toml` supplies additional `permission.<key>` toggles
verbatim — currently `skill`, `webfetch`, `websearch`, `codesearch`.

`opencode/AGENTS.md` → the global instructions document. It is a document at a path, like the
other three harnesses — **not** the `instructions` key in `opencode.json`, which is a separate
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

**Do not reach for `OPENCODE_DISABLE_EXTERNAL_SKILLS`.** It is the bigger hammer
and removes `.agents` as well as `.claude`, which costs you your own `.agents`
skills for nothing — loadout writes none.

`.agents/skills` is scanned unconditionally and neither flag removes it. That is
safe here only because **loadout writes nothing to `.agents/`** — a fact about
loadout, not about OpenCode. Adding an `.agents/skills` destination would
recreate the race in a place no flag can disable.

**Why the race and not an ordering.** The duplicate branch warns and does not
return, so the assignment after it runs anyway:

```ts
if (state.skills[md.data.name]) { yield* Effect.logWarning("duplicate skill name", …) }
state.skills[md.data.name] = { … }        // runs unconditionally
```

A `return` there would make it first-wins, which — with a fixed discovery order —
would be a precedence rule. Without one it is whichever concurrent read lands
last. "Warn on duplicate" reads like a guard and is not one.
