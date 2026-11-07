# Claude Code

## Config files

Read in precedence order, highest first:

| scope | path |
|---|---|
| managed | `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS), plus `managed-settings.d/*.json` |
| CLI args | temporary session overrides |
| local | `.claude/settings.local.json` |
| project | `.claude/settings.json` |
| user | `~/.claude/settings.json` |

**One canonical file per scope. No imports, no includes, no splitting.** The
`managed-settings.d/` drop-in directory is the sole exception and is managed-scope only.

MCP server configuration and per-project state live in `~/.claude.json`, not in
`settings.json`. Project-scoped MCP servers live in `.mcp.json`.

`CLAUDE_CONFIG_DIR` replaces `~/.claude` wherever it appears — `settings.json`, `CLAUDE.md`,
`ide/`, `teams/` — and `~/.claude.json` moves with it, resolving to
`$CLAUDE_CONFIG_DIR/.claude.json`. Verified 2026-08-09 against 2.1.226. See
[Relocating the config directory](README.md#relocating-the-config-directory).

## Resolution

`deny → ask → allow`, and **rule specificity does not change the order**. Two
consequences that matter:

- A broad deny cannot carry allowlist exceptions. `Bash(aws *)` in deny blocks
  `aws s3 ls` even when that exact string is in allow.
- "If a tool is denied at any level, no other level can allow it."

**Permission rules merge across scopes rather than override.** This is the one setting
that does not follow the normal precedence hierarchy — allow and deny from every scope
are simultaneously in force. It is also why permissions cannot be moved to a different
scope to separate them from hand-maintained settings: they would still merge, but the
hand-maintained keys would not.

## Pattern shape

```
Bash(<entry>:*)     plain command prefix
Bash(<entry>)       entry ending in `*` — Claude's matcher handles the glob
Bash(<command>)     exact entry `=<command>` — no `:*`, so only the whole command
mcp__<server>__<tool>
```

**`Bash(x)` without `:*` is exact — verified 2026-10-06 against Claude Code 2.1.292 by reading
the installed binary**, not by a live probe: a nested `claude -p` could not authenticate from
the sandboxed session that checked it, so the probe below is still the stronger evidence and
worth repeating. The embedded rule classifier sorts a Bash rule into three types:

    function Cit(e){let n=HAe(e);if(n!==null)return{type:"prefix",prefix:n};
      if(Lpo(e))return{type:"wildcard",pattern:e};return{type:"exact",command:e}}

`HAe` matches `^(.+):\*$` and `Lpo` an unescaped `*`. The Bash permission matcher then
tests an `exact` rule with `case"exact":return ke.command===Se`, string equality against the
command and its normalised forms — leading `VAR=value` assignments and a `timeout`/`time`
wrapper stripped — so `Bash(git status)` matches `git status` and `FOO=1 git status`, never
`git status --short`.

**Verified 2026-08-01 against Claude Code 2.1.220**, sandboxed `HOME`, with
`Bash(touch alpha:*)` as the only allow rule:

| probe | result |
|---|---|
| `touch alpha` (bare) | ran, no denial |
| `touch alpha beta` | ran, no denial |
| `touch zulu` (control) | denied, explicit `permission_denials` record |

So `:*` matches both the bare command and the command with arguments. This syntax is
Claude-specific — do not carry it into another harness's output.

## What loadout emits

From `GLOBAL_PRESET["claude"]` in `harnesses.py` and `PROJECT_PRESET["claude"]` in `project.py`.
Global paths are under `${CLAUDE_CONFIG_DIR:-~/.claude}` unless shown otherwise.

| slice | global | project |
|---|---|---|
| permissions | the `permissions` key of `settings.json` (`render_claude`), with the `[claude] settings` fragment as the rest of the document | the `permissions` key of `.claude/settings.json` (`render_claude_project`), with the existing file as the rest |
| hooks | the `hooks` key of `settings.json` | not in the project preset |
| plugins | the `enabledPlugins` key of `settings.json` | not in the project preset |
| mcp-permissions | `mcp-permissions.json`, fully owned `PermissionRequest` hook policy, written only when `[mcp]` has a rule | `.claude/mcp-permissions.json`, the same |
| MCP server definitions | the `mcpServers` key of `${CLAUDE_CONFIG_DIR:-~}/.claude.json` ([servers.md](servers.md)) | `.mcp.json`, fully owned |
| instructions | `CLAUDE.md` | `CLAUDE.md`; with another harness declared, a stub importing `@AGENTS.md` |
| skills | `skills/` | `.claude/skills/` |
| agents | `agents/` | `.claude/agents/` |
| module config | authored paths beneath the root, such as a hook script the hooks slice registers by path | not in the project preset |

`permissions` is written as `allow`, `deny`, `ask` globally and `allow`, `ask`, `deny` in a
project. Any other key inside it, such as `defaultMode`, comes from the settings fragment, or at
project scope from the existing file.

An autonomous variant is a profile, not a second output: a sibling such as `autonomous.toml`
that `extends` the default, sets `permissions = []` under `[claude]` so all three lists render
empty, and composes its own settings fragments. Clearing `deny` is what lets the auto-mode
classifier judge every call, `git push` included, since deny overrides every mode. Whichever
profile is active renders to the same `settings.json`. See
[composition.md](composition.md).

## `CLAUDE_AFK_TIMEOUT_MS`

Verified 2026-08-03 by inspecting the installed 2.1.220 bundle:

```js
enabled   = !… && !payload.hasExternalRacer && !… && (afkTimeoutMs !== null || Z.CLAUDE_AFK_TIMEOUT_MS !== void 0)
timeoutMs = Z.CLAUDE_AFK_TIMEOUT_MS ?? props.timeoutMs ?? DEFAULT
```

- It governs **`AskUserQuestion` auto-submit**, not permission prompts — the surrounding
  code is question-state machinery (`currentQuestionIndex`, `answersToSubmit`,
  `setAnswer`). This is why permission prompts are never seen auto-forwarding.
- The enabling test is strict `!== null`, so an **omitted** prop (`undefined`) passes.
  The timer is therefore **on by default** unless a call site explicitly opts out
  with `null`.
- There is **no "off" value**. Setting the variable to anything force-enables the
  machinery even where a caller opted out, and only changes the duration.

`~/ac` sets it to `2147483647` (≈25 days) in the interactive profile, which does not
disable the feature — it makes the fuse unreachable. Its autonomous profile leaves that
settings fragment out, which is what restores the short default, so the difference between the
two profiles is load-bearing. Composing the fragment into the autonomous profile would give
headless runs a 25-day fuse.

**Not verified:** whether the component mounts under `-p`, and the value of the default
constant — so the "60s" figure in `~/ac`'s own comment is unconfirmed.

## Deployment

`~/.claude/settings.json` is a real file that loadout generates whole from the source: the
settings fragment plus the `permissions`, `hooks` and `enabledPlugins` keys. Nothing is read back
from the live file except the keys a `preserve` list under `[claude]` names, and `preserve`
refuses a key loadout generates.

Claude Code also writes to this file at runtime (`enabledPlugins`, `extraKnownMarketplaces`,
`env` model upgrades, `/fast`, `/config` toggles). Such a write is not in the source, so `check`
reports the file as `hand-edited` drift and `sync` refuses to overwrite it until the edit is
ported into the source with `/loadout` or discarded with `sync --force`. A key Claude Code keeps
for itself belongs in `preserve`, which carries it from the live file on every render.

At project scope `.claude/settings.json` is rendered over the existing file (`preserve_foreign`
in `PROJECT_PRESET`), so keys loadout does not write keep their place.

## Account skills: `skills/synced`

Claude Code syncs the skills of the signed-in claude.ai account into
`${CLAUDE_CONFIG_DIR:-~/.claude}/skills/synced/`: a `.bucket-<org>_<account>` marker beside an
`<org>_<account>` folder that holds one folder per skill, `manifest.json`, `.staging` and
`.last-complete-round` (this machine, 2026-10-22). Verified 2026-10-22 in the installed 2.1.307
binary:

- the folder name is reserved, beside the sync's other names:
  `var QW="synced",E$r=".trash",hJ=".staging"`;
- the sync claims the folder as its own, beside its trash and staging folders:
  `syncOwnedPrefixes:[...s].flatMap((y)=>[wp(y,QW)+f_,wp(y,E$r)+f_,wp(y,".staging")+f_])`;
- the user skills are loaded from the bucket folder under their own source,
  `IL(wp(s,QW,xt),"userSettings","syncedSkills")`, and tagged `loadedFrom==="syncedSkills"`;
- the bucket marker prefix is `var dn=".bucket-"`.

Claude Code writes the folder itself, so neither `check` nor `init` treats it as the user's: it
is not reported as `unmanaged` and not adopted into the source. The exemption is for that one
folder, directly in Claude's skills folder; a `synced` anywhere else, such as in Codex's skills
folder or inside a skill, is the user's (`HARNESS_RUNTIME_ENTRIES` in `runtime_paths.py`).
A source skill named `synced` that would render into Claude's skills folder is refused with exit 3
by `sync`, `check` and `init`, since it would land on the account's skills; rename the skill.

## Stealth sources

A [stealth source](../user/guides/stealth.md) writes `CLAUDE.local.md`, `.claude/settings.local.json`
(keys and rules it owns, beside whatever Claude saved there), `.claude/skills/<name>/` and, for MCP
servers, leaves of `~/.claude.json` at `projects.<repository>.mcpServers.<name>`
([0048](../decisions/0048-nested-json-ownership-claude-json-leaves-and-owned-keys-in-shared-documents.md)).

**`@AGENTS.md`.** Claude reads `AGENTS.md` only without a `CLAUDE.md` or `CLAUDE.local.md` in the
working directory or above it. Run on 2.1.314 (`claude -p`, scratch config directory, 2026-10-30):
a `CLAUDE.local.md` whose first line is `@AGENTS.md` loads `AGENTS.md` exactly once, also under
`claude-md-and-agents-md`; without the import the team's `AGENTS.md` is dropped; and with no
`AGENTS.md` the line is skipped silently, exit 0, nothing in the debug log
([0049](../decisions/0049-the-conditional-agents-md-line-and-opencode-instructions-by-absolute-path.md)).
The memory docs say the same: "Because `CLAUDE.local.md` counts, adding one to keep your own
uncommitted instructions in a project that relies on `AGENTS.md` stops Claude from reading
`AGENTS.md` for you." Not established: the interactive UI.

**The lock.** Claude Code saves `~/.claude.json` under proper-lockfile. Verified 2026-11-03 in the
installed 2.1.317 binary: the strings contain ``lockfilePath:`${y}.lock` `` and `stale:1e4`, so the
lock is a directory named `<config path>.lock`, made with `mkdir`, and taken over once ten seconds
old. The config path is the config directory joined with `.claude.json` and is never resolved, so a
symlinked `~/.claude.json` is locked at the link's path. Loadout joins that lock
(`stealth_lock.claude_json_locks`) and never breaks it; the global scope's `~/.claude.json` write
takes the same lock around its read, merge and replace, and around retiring loadout's keys, as the
stealth source does. A held lock is waited on for 15 seconds, then sync exits 3 naming the directory and
writes nothing to the file; loadout refreshes its own directory's mtime every five seconds so
Claude never takes it over ([sync.md](sync.md#files-a-harness-locks)).

**Files Claude reads instead.** Verified in the same binary:
`function h_(){…let o=U($e(),".config.json"),t=cAr();try{let _=le().statSync(o);if(_.isFile()&&(_.size>0||(_.mode&146)!==0&&!le().existsSync(t)))return o}catch{}return t}`.
A legacy `.config.json` in the config directory that has content, or is writable while
`.claude.json` is absent, is the file Claude reads and writes. And
``cAr()`` builds ``.claude${P1()}.json`` with
`function P1(){if(process.env.CLAUDE_CODE_CUSTOM_OAUTH_URL)return"-custom-oauth"…`, so that variable
makes the config `.claude-custom-oauth.json`. Loadout refuses to write MCP servers in either case,
naming the file Claude reads, rather than write a file Claude ignores. A relative
`CLAUDE_CONFIG_DIR` is refused too.

Claude's `settings.local.json` also receives approvals Claude saves ("don't ask again"); a
stealth source carries those after its own rules.
