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

OpenCode names an MCP tool `<server>_<tool>` with every character outside `[a-zA-Z0-9_-]`
replaced by `_` in each half (`sanitize` and `toolName` in `mcp/catalog.ts`, v1.18.15), so
`svc.two/read` renders as `"svc_two_read"`. `*` and `?` are kept, being wildcards there.
`render_opencode` refuses two servers whose names sanitise alike, and two targets that become
one key, such as `a_b/c` and `a/b_c`, since a rule keyed by that name matches both.

A tool name may itself hold `_`, the separator, so a rule for one server can reach another's
tools: `a/*` is `a_*`, which also matches `a_b_read`, a tool of server `a_b`, and `a_b/c` is
`a_b_c`, which also matches `a`'s tool `b_c`. Render refuses such a rule, whatever its decision,
naming both servers, against every server the same source names in a rule or in `mcp.toml`.
**A server the source does not name cannot be checked**: one added with `opencode mcp add`, or,
for a project, one declared only at global scope. A rule can still reach its tools.

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

**Until this destination existed, OpenCode v1 fell back to `~/.claude/CLAUDE.md`** — which
loadout also writes. OpenCode was reading Claude's document, with Claude's fragments in it, and
the fallback is silent, so nothing looked broken. The fallback is v1's only. Verified negative
by run on 2.2.5 (2026-11-02): with no `AGENTS.md` anywhere, neither `~/.claude/CLAUDE.md` nor a
repository `CLAUDE.md` reached the request, and the server watched only
`~/.config/opencode/AGENTS.md` and the repository's `AGENTS.md`. Claude's skill folders are still
read in 2.2.5, and `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` still turns them off.

## Gotcha: v2 renames nearly every key

OpenCode v2 renames most configuration keys, including
`mcp.{server}.enabled` → `mcp.servers.{server}.disabled` — a **silent boolean
inversion**. A config that appears to migrate cleanly will have every server's
enabled state flipped.

## v2 reads loadout's v1 keys

loadout writes OpenCode's v1 keys, `permission` and `mcp`. Read from the v2.2.0 source
(`anomalyco/opencode` at `v2.2.0`, 2026-11-01), v2 migrates both when it loads the file
(`packages/core/src/config/normalize.ts`):

- **`mcp`.** The four fields loadout writes, `type`, `command`, `url` and `headers`, are kept
  (`packages/core/src/v1/config/migrate.ts:81-108`). `{env:VAR}` is expanded on the raw file text
  before migration (`packages/core/src/config/variable.ts:25-31`).
- **`permission`.** `allow`, `ask` and `deny` map one to one, and `bash` becomes `shell`. Order is
  kept, and evaluation is still last-match-wins.
- **Unknown fields vanish.** A field that is not in the v1 schema is dropped with no diagnostic
  (`onExcessProperty: "ignore"`, `normalize.ts:44`). Every field loadout writes today is in it; a
  renderer change that adds one must confirm v2 keeps it.
- **Enabled-only entries.** A legacy MCP entry that holds only `enabled` is dropped with a
  diagnostic (`normalize.ts:271-273`). loadout never writes one.
- **Diagnostics are log lines.** Every migration diagnostic goes only to a log warning
  (`packages/core/src/config.ts:145-153`). A native `permissions` key outranks v1 `permission`, a
  bad rule is dropped on its own, and a file that fails validation is ignored whole.

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

## Stealth destinations

A [stealth source](../user/guides/stealth.md) writes a root `opencode.json` and `.opencode/skills/`,
refused when the team tracks either. The file is shared: the stealth source owns its `permission`, `mcp`
and `instructions` keys.

Run on OpenCode 2.2.5 (2026-11-02, scratch home, stub MCP servers and scripted `shell` calls) and
read from the 2.2.0 source and docs:

- **`permission`.** The user's and the project's rules are concatenated in load order and the last
  match wins, so a project rule beats a user rule for the same pattern and the user's unrelated
  denies survive. A repeated pattern moves to the end; it is not a key merge. The v1 `permission`
  object still takes effect in 2.2.5. The v2 `permissions` key takes only the v2 array: a v1-shaped
  object there is dropped with a log-only warning, so the stealth source keeps writing `permission`.
- **`mcp`.** Merged per server name. A project server of a user server's name replaces it whole
  (no `environment` is carried over), unlike Codex, which is why the stealth source refuses the name.
- **`instructions`.** A union across documents, user first, duplicates removed. An absolute path
  outside the repository loads, so the stealth source names its own rendered file by absolute path
  ([0049](../decisions/0049-the-conditional-agents-md-line-and-opencode-instructions-by-absolute-path.md)).
- **A tracked `.opencode/opencode.json` outranks the root file.** Its same-name server and later
  rule win; the stealth source's other entries still apply. `check` names each.
- **Skills.** Untracked and tracked skills in `.opencode/skills/` are both offered, also when
  `info/exclude` ignores them. OpenCode also reads `.claude/skills/` unless
  `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`, so a skill rendered for both reaches it twice; `check`
  says so.
- **Trust.** `opencode run` showed no trust gate or prompt on a fresh home. Not checked: the TUI.
- **No catch-all.** The stealth source writes no `"*"` entry under `permission.bash` unless the stealth source's
  permissions state `[shell] default`, so it never changes the verdict for a command no rule
  names.
