# Cursor CLI

Cursor's terminal agent (`agent`, formerly `cursor-agent`) is a supported Loadout harness under
the name `cursor` ([0054](../decisions/0054-cursor-cli-is-a-supported-harness.md)). This page
records what Cursor does, and what loadout writes for it is in [What loadout writes](#what-loadout-writes).

Sources, in the order AGENTS.md ranks them:

- **Shipped docs:** none. The package ships no documentation.
- **The binary:** the `2026.11.05-86047df` darwin-arm64 package, which is the version
  `https://cursor.com/install` pointed at on 2026-11-08. It was downloaded and extracted into a
  scratch directory and read statically, never installed or run. The bundle is minified, so the
  citations below quote code, not symbol names.
- **Upstream docs:** `https://cursor.com/docs/…​.md`, read 2026-11-08.

Nothing on this page was observed with a signed-in agent run, since every one needs a Cursor
account, so the behavioural claims, the headless one most of all, are still unverified. One thing
was observed in a throwaway container without an account: given a `cli-config.json` holding only
`permissions`, `agent --version` rewrote it with about twenty defaults (`version`, `editor`,
`display`, `approvalMode`, `sandbox`, `attribution` and more) appended after the key, which kept its
place.

## Destinations

The config-directory variable is `CURSOR_CONFIG_DIR`, and it names the directory itself, not a
home. In the bundle, `cursor-config/dist/paths.js` resolves it like this:

    const t=process.env.CURSOR_CONFIG_DIR;if(t?.trim())return t;
    const e=process.env.XDG_CONFIG_HOME;return e?.trim()?(0,i.join)(e,"cursor"):(0,i.join)((0,s.homedir)(),".cursor")

Two things in that code differ from the docs. The configuration page says `XDG_CONFIG_HOME` applies
on "Linux/BSD", but the code applies it on every platform, macOS included. And the variable moves
**only** `cli-config.json` and `permissions.json`, the two files built from that function. Every
other global file is joined directly onto `homedir()`, so it does not follow the variable:

| slice | global | project | global path follows `CURSOR_CONFIG_DIR` |
|---|---|---|---|
| shell permissions | `cli-config.json` → `permissions` | `.cursor/cli.json` → `permissions` | yes |
| Auto-review instructions | `permissions.json` | `.cursor/permissions.json` | yes |
| instructions | none, see below | `AGENTS.md` (and `CLAUDE.md`, `CLAUDE.local.md`) | — |
| project rules | — | `.cursor/rules/*.mdc` | — |
| MCP server definitions | `~/.cursor/mcp.json` | `.cursor/mcp.json` | no: `join(homedir(),".cursor","mcp.json")` |
| hooks | `~/.cursor/hooks.json` | `.cursor/hooks.json` | no: `userConfigPath:u.join((0,v.homedir)(),".cursor","hooks.json")` |
| skills | `~/.cursor/skills/` | `.cursor/skills/` | no: skill roots are joined onto `this.userHomeDirectory` |
| agents (subagents) | `~/.cursor/agents/` | `.cursor/agents/` | no |
| plugins | `~/.cursor/plugins/` (app-managed) | — | no |

So [0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) holds for the
permissions destination only. Relocating Cursor leaves its MCP, hooks, skills and agents at
`~/.cursor`, because Cursor itself leaves them there. A destination template has to repeat that
split rather than put every slice under the variable.

Project `cli.json` is read from **every** `.cursor/` between the workspace root and the current
directory, not only the root's. The bundle walks each path segment and collects every
`join(t,".cursor","cli.json")` that exists. The configuration page also says "Only permissions can
be configured at the project level", so project `cli.json` holds permissions and nothing else.

Workspace trust is per-folder. `--trust` is "Trust the workspace without prompting (headless mode
only)", and the bundle's protected-path list includes `**/.workspace-trusted`. Project hooks run
"in any trusted workspace".

## Instructions

Cursor walks from the workspace **up to the filesystem root**. In every ancestor directory it
loads `.cursor/rules/`, `AGENTS.md`, and also `CLAUDE.md` and `CLAUDE.local.md` when third-party
imports are on (they are by default; see below). From `LocalCursorRulesService.loadRulesFromDirAndAncestors`:

    a&&r.push("CLAUDE.md","CLAUDE.local.md"),r.push("AGENTS.md");… const c=(0,s.dirname)(o);if(c===o)break;o=c

The CLI usage page states the project half: "The CLI also reads `AGENTS.md` and `CLAUDE.md` at the
project root (if present) and applies them as rules alongside `.cursor/rules`." Both files are
loaded as `alwaysApply: true`.

- **Project:** the repository-root `AGENTS.md`, the one Codex, Droid, OpenCode and Pi already
  share. Cursor also reads the project `CLAUDE.md` that loadout writes, which holds only an
  `@AGENTS.md` import ([0027](../decisions/0027-project-claude-md-imports-agents-md.md)). Cursor
  does not inline that import. The rules page says: "A file mention like `@migration-template.sql`
  tells Agent where to look … The file's contents are not inlined into the prompt".
- **Global:** verified negative, there is no global instructions file in the 2026.11.05 bundle.
  The rules loader above is the only file-based instructions source, and it reads workspace
  ancestors, not the config directory. The bundle contains no `".claude","CLAUDE.md"` join, so
  Claude's global `~/.claude/CLAUDE.md` is not read either. The rules page puts "User Rules" in
  **Customize → Rules**, an app setting, not a file. A `~/AGENTS.md` would apply to every project
  under the home directory, but only as an ancestor. It is not a config-directory file.

## Shell permissions

Permissions are `permissions.allow` and `permissions.deny`, arrays of typed tokens: `Shell(…)`,
`Read(…)`, `Write(…)`, `WebFetch(…)`, `Mcp(server:tool)`. The bundle's parser also accepts `Bash(…)`
as a synonym: `t.match(/^\s*(Shell|Bash)\s*\((.*)\)\s*$/)`. The permissions page lists all of these
types.

There are two lists. There is no ask list: a command not allowed "will ask you to approve (y) or
reject (n)". So loadout's `ask` has nowhere to go, and an ask narrower than an allow
(allow `git`, ask `git push`) cannot be expressed.

**Resolution:** "Deny rules take precedence over allow rules" (permissions page), and the bundle
checks deny first (`hasHardDeny`). Sources are combined as a **union** of every provider:
`for(const t of i.allow)e.add(t);for(const e of i.deny)t.add(e)`. The providers are global
`cli-config.json`, each project `cli.json`, and `permissions.json`. So the lists are
order-independent, and a project cannot narrow away a global allow except by denying.

**Matching**, from `matchesShell` in the bundle. The entry is `Shell(<full>)`, optionally written
`Shell(<cmd>:<args>)`. It matches a command when any of these holds:

- `cmd` glob-matches the base command (or the whole command), **and** `args` glob-matches the rest;
- `full` glob-matches the whole command;
- the command starts with `full` followed by a space. This is a word-boundary prefix, so
  `Shell(git status)` matches `git status -s`;
- `full` glob-matches, or equals, the base command (the first word).

The globs are picomatch-style with `dot: true`. `*` does not cross `/`, so `Shell(cat *)` does not
match `cat a/b`. That is not yet verified at runtime. A compound command is split by a bash
parser (tree-sitter), and **every** part must be allowed. A command the parser cannot parse
always prompts: `"Parser failed to parse command (possible bypass)"`.

**Catch-all:** there is no shell default in the document. What happens to an unlisted command is
set by `approvalMode` (`allowlist`, `auto-review` or `unrestricted`), which is a CLI setting in
the same `cli-config.json`. Under [0016](../decisions/0016-a-catch-all-is-stated-only-where-the-document-carries-it.md)
that makes it a catch-all stated elsewhere, like Claude's.

**MCP tool policy** lives in the same lists as `Mcp(server:tool)`, with `*` wildcards
("`Mcp(datadog:*)`", "`Mcp(*:*)`").

**Headless** (ADR 0012's first test): the parameters page describes `--force` as "Force allow
commands unless explicitly denied". So deny entries bind even under `--force`, and without it the
allowlist decides. The CLI usage page also says "Cursor has full write access in non-interactive
mode". Whether an unlisted shell command is refused or auto-run in `-p` mode without `--force`
has **not** been observed.

### `cli-config.json` is a shared document

The CLI writes runtime state into the same file. The bundle's default config includes
`hasChangedDefaultModel`, `runEverythingSettingsPromptStreak`,
`runEverythingSettingsPromptCooldownUntilMs`, `privacyCache` and `authInfo`. The configuration page
lists `editor.vimMode` as a **required** field, and adds: "CLI performs self-repair for missing
fields", "Some fields are CLI-managed and may be overwritten", and "Corrupted files are backed up
as `.bad` and recreated". The file is "Pure JSON format (no comments)".

So loadout can own the `permissions` key only, and must preserve everything else, the way it
treats Codex's `config.toml`.

`permissions.json` (global and project) is a separate surface, read by both the CLI and the app.
It holds Auto-review's plain-English `allow_instructions` and `block_instructions`, plus
`terminalAllowlist` and `mcpAllowlist`. The bundle turns the last two into `Shell(…)` and `Mcp(…)`
allow entries.

## MCP server definitions

Servers live in `mcp.json`, at `~/.cursor/mcp.json` globally and `.cursor/mcp.json` per project,
under `mcpServers`. A stdio server has `command`, `args`, `env` and `envFile`. A remote server has
`url`, `headers`, and static OAuth as `auth.CLIENT_ID` and `auth.CLIENT_SECRET`. The config is
interpolated: `${env:NAME}`, `${userHome}`, `${workspaceFolder}`, `${workspaceFolderBasename}`,
`${pathSeparator}`. Variables are written `${env:NAME}`, not `${NAME}`. All of this is from the
MCP page.

Project servers need approval. `--approve-mcps` is "Automatically approve all MCP servers", and
`agent mcp enable <identifier>` adds "an MCP server to the local approved list". The approvals
are kept in `mcp-approvals.json` under the per-project data directory, not in the project.

## Skills

The skill roots come from the bundle's table, in its order:

| root | gated by third-party imports |
|---|---|
| `.cursor/skills` | no |
| `.claude/skills` | yes |
| `.codex/skills` | yes |
| `.grok/skills` | yes |
| `.agents/skills` | no |

Each root is read at both scopes: the project, and `homedir()` (`ki(this.userHomeDirectory,…)`).
The skills page agrees: "Cursor also loads skills from Claude and Codex directories:
`.claude/skills/`, `.codex/skills/`, `~/.claude/skills/`, and `~/.codex/skills/`". Nested
`.cursor/skills` and `.agents/skills` folders inside a repository are also discovered, and scoped
to their subtree.

So Cursor reads `.agents/skills` at both scopes, which makes it a member of `SHARED_SKILL_READERS`
by that table's own definition. With third-party imports on, it also reads the skills loadout
already renders for Claude and Codex. How it resolves two roots holding the same skill name has
not been established.

The frontmatter it reads (skills page): `name` (which must match the folder), `description`,
`paths`, `disable-model-invocation`, `icon`, `color` and `metadata`.

## Agents (subagents)

Agents are Markdown with YAML frontmatter, in `.cursor/agents/` and `~/.cursor/agents/`. With third-party
imports, `.claude/agents/`, `.codex/agents/`, `~/.claude/agents/` and `~/.codex/agents/` are read
too. The subagents page says: "When multiple locations contain subagents with the same name,
`.cursor/` takes precedence over `.claude/` or `.codex/`."

Fields: `name` (default: the filename), `description`, `model` (`inherit` or a model ID),
`readonly` and `is_background`. **Effort is not a key.** It is a parameter inside the model ID,
`claude-opus-5[effort=high]`, and "Available options depend on the model". So `EFFORT_KEY` does
not fit. An effort renders into `model`, and only when the model is known.

## Hooks

Hooks have a native document, `hooks.json`, with `{"version": 1, "hooks": {<event>: [{command,
type, timeout, loop_limit, failClosed, matcher}]}}`. User hooks run from `~/.cursor/` and project
hooks from the project root (hooks page).

The agent events: `sessionStart`, `sessionEnd`, `preToolUse`, `postToolUse`, `postToolUseFailure`,
`subagentStart`, `subagentStop`, `beforeShellExecution`, `afterShellExecution`,
`beforeMCPExecution`, `afterMCPExecution`, `beforeReadFile`, `afterFileEdit`, `beforeSubmitPrompt`,
`preCompact`, `stop`, `afterAgentResponse` and `afterAgentThought`. There are also the Tab events
`beforeTabFileRead` and `afterTabFileEdit`, and `workspaceOpen`. This list is quoted from the
hooks page's own sentence listing them, not assembled by search.

**Cursor also runs Claude's hooks.** The bundle's hook paths include
`claudeUserConfigPath:…".claude","settings.json"`, plus the project `.claude/settings.json` and
`.claude/settings.local.json`. The third-party hooks page maps Claude events onto Cursor's:
`PreToolUse→preToolUse`, `UserPromptSubmit→beforeSubmitPrompt`, and so on. A Claude hook whose
`command` string is identical to a Cursor hook on the same event is dropped as a duplicate:
`Removed duplicate ${t} hook for ${o}`.

## Third-party imports

One switch decides whether Cursor reads Claude's `CLAUDE.md`, skills, agents, hooks and plugins.
The third-party hooks page names it **Include Third-Party Plugins, Skills, and Other Configs**,
under Settings → Agents → Third-Party Imports, and says: "The setting is on by default." In the CLI
bundle the getter defaults to true (`e??(()=>!0)`). Whether the CLI exposes a way to turn it off
has not been found.

This matters more for Cursor than anything else on the page. Out of the box, Cursor already
consumes most of what loadout renders for Claude. Rendering Cursor's own copies on top of that
gives it duplicates, unless the slice relies on the import instead.

## Plugins

Cursor plugins have a `.cursor-plugin/plugin.json` manifest and are installed from a marketplace
by the app. Local development plugins go in `~/.cursor/plugins/local` (plugins page). Claude
plugins are imported when third-party imports are on. The bundle filters
`"claude-plugin"!==t.identifier.source` only when `importThirdPartyPlugins` is off.

Verified negative, as of 2026-11-08: there is no declarative enablement key in `cli-config.json`
like Claude's `enabledPlugins`. None is listed on the configuration page's schema, and none is in
the bundle's default config. The plugin enablement mechanism is therefore **not established**.

## Measured against 0012

| 0012's test | Cursor CLI 2026.11.05 |
|---|---|
| The generated file is honoured where it matters | interactive: yes, per the docs. Headless: deny binds even under `--force`, and the allowlist governs without it, per the docs. Runtime: **unverified** |
| A global skills path | yes: `~/.cursor/skills/`, plus `~/.agents/skills/` |
| A config-directory variable | yes, `CURSOR_CONFIG_DIR`, but it moves only `cli-config.json` and `permissions.json` |
| The plugin enablement mechanism | **not established**: app-managed, with no file key |
| Globs | yes: picomatch, and `*` does not cross `/` |

## What loadout writes

| slice | global | project |
|---|---|---|
| permissions | the `permissions` key of `${CURSOR_CONFIG_DIR:-~/.cursor}/cli-config.json` | `.cursor/cli.json` |
| mcp | `~/.cursor/mcp.json` | `.cursor/mcp.json` |
| hooks | `~/.cursor/hooks.json` | `.cursor/hooks.json` |
| instructions | — | `AGENTS.md`, shared with Codex, Droid, OpenCode and Pi |
| skills | `~/.cursor/skills/` | `.cursor/skills/`, or the shared `.agents/skills` copy beside Codex |
| agents | `~/.cursor/agents/` | `.cursor/agents/` |
| module-config | beneath `~/.cursor` | — |

`CURSOR_CONFIG_DIR` unset, the permissions destination follows `XDG_CONFIG_HOME` the way Cursor
does: `$XDG_CONFIG_HOME/cursor/cli-config.json` when that is set, `~/.cursor/cli-config.json`
otherwise.

**Shell entries:**

| source | rendered | why |
|---|---|---|
| `git status` | `Shell(git status)` | the word-prefix rule matches it bare and with arguments |
| `=git status` | `Shell(git:status)` | the `cmd:args` form matches the arguments whole, so it is exact |
| `gamma-*` (allow or deny) | `Shell(gamma-*)` | a glob within the first word also matches that word alone, whatever follows |
| `git log *` (allow) | `Shell(git log *)` | literal; an argument holding `/` does not match, so it prompts |
| `git log *` (deny) | `Shell(git log)` | widened to the word prefix, which blocks every argument; a notice says so |
| an ask | nothing | there is no ask list; `approvalMode` decides, and a notice says so |
| an allow reaching an ask | nothing | it would approve what the source asks about; a notice says so |
| `svc/read` | `Mcp(svc:read)` | |

`[shell] default` is not rendered: Cursor's catch-all is `approvalMode`, a CLI setting in the same
file, which loadout does not own.

**Hooks** are Cursor's own translation of a Claude hooks document, ported from the bundle:

- the events map `PreToolUse→preToolUse`, `PostToolUse→postToolUse`,
  `UserPromptSubmit→beforeSubmitPrompt`, `Stop→stop`, `SubagentStop→subagentStop`,
  `SessionStart→sessionStart`, `SessionEnd→sessionEnd` and `PreCompact→preCompact`;
- `Bash` matches as `Shell`, and `Edit` as `Write`;
- `mcp__server__tool` matches as `MCP:tool`, without the server;
- `Glob` has no Cursor tool;
- every hook carries `loop_limit: null` and `failClosed: false`.

`PermissionRequest`, `Notification` and any other event are left out and reported. Cursor sets
`CLAUDE_PROJECT_DIR` as an alias, so a hook using it renders. Any other `CLAUDE_` variable is
refused.

**Agents** carry `name`, `description` and `model`, plus anything under a `cursor:` block (such as
`readonly` or `is_background`). Cursor reads frontmatter as `key: rest-of-line` and does no YAML
unquoting, so a one-line description is written plain. An effort is noted rather than written:
give it in the model, as `cursor: claude-opus-5[effort=high]`.

**Init** reads `cli-config.json`'s `permissions` and never the rest, which is the CLI's. A deny
token the source cannot hold refuses the run, because dropping it would allow what it blocks. That
covers `Read(…)`, `Write(…)`, `WebFetch(…)`, and `Shell(cmd:args)` with a wildcard in it. A hook
with `failClosed`, a `loop_limit`, an `MCP:` matcher or a Cursor-only event refuses it too.

## Stealth destinations

A [stealth source](../user/guides/stealth.md) writes `.cursor/rules/<name>.mdc`,
`.cursor/cli.json`, `.cursor/mcp.json` and `.cursor/skills/<name>/`, each a whole file, all hidden
through `.git/info/exclude`. Each was checked against how the 2026.11.05 bundle finds it, because a
file git ignores is not always a file Cursor reads:

- **Instructions are a rule**, `alwaysApply: true`, named for the stealth source as Codex's rules
  file is. The bundle lists `.cursor/rules` with its own ripgrep:
  `rg --files --hidden --no-require-git … --iglob **/*.mdc`, with no `--no-ignore-vcs`. An
  include glob overrides ignore rules in ripgrep, so an excluded rule is still found. That was
  measured with the host's ripgrep 15.2.0 on a scratch repository: with Cursor's arguments, an
  `.mdc` listed in `.git/info/exclude` or `.gitignore` was listed; without the glob it was not.
  Cursor ships its own `rg` build, which was not run. The repository-root `AGENTS.md`, which
  Cursor always reads, is usually the team's, and `CLAUDE.local.md` is read only while the
  third-party imports are on.
- **`cli.json` is owned whole.** The CLI reads every project `cli.json` and writes none: its one
  writer, the config provider's `transform`, writes the global `cli-config.json` only. Only
  `permissions` is read from a project file, and it is written only when the stealth source has a
  rule Cursor can hold.
- **`mcp.json`** is read by a direct join, `join(t,".cursor","mcp.json")`. By default the loader
  keeps a project and a user server of the same name as two servers, under `project-…-` and
  `user-` prefixes, and the combined view keeps the first scope's whole entry, never a merge of fields. Which
  of the two an agent ends up calling was not established, so a stealth source refuses a server name
  `~/.cursor/mcp.json` already defines, as it does for the other harnesses. Project servers need
  approval: `agent mcp enable`, or `--approve-mcps`.
- **Skills** are found by a `readdir` walk of each root (`includeFile: t=>"SKILL.md"===t`), with no
  ignore check, so excluded folders are read. A skill identical for Cursor and Codex, OpenCode or Pi
  is one `.agents/skills/` copy.

With third-party imports on, a stealth source that also declares Claude reaches Cursor twice:
through `CLAUDE.local.md`, and through `.claude/skills/` for each skill both harnesses get.
`check` names both. Nothing here was run with an account.
