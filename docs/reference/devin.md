# Devin CLI

Cognition's terminal agent, `devin` (also called Devin for Terminal). This page records what Devin
does. Whether and how loadout targets it is decided separately.

Sources, in the order AGENTS.md ranks them:

- **Shipped docs:** the 44 `.mdx` pages under `share/devin/docs/` in the platform package. The
  binary also serves them as its built-in `devin-cli` skill. Every quotation below is from these
  pages; the same text is published at `https://docs.devin.ai/cli/…​.md`, read 2026-11-19.
- **The binary:** `devin 3000.14.30821 (a09cd7f323ee)`, the npm package `devin@3000.14.30821`
  for linux-arm64. It was run on 2026-11-19 in a throwaway `node:22-bookworm` container, with a
  scratch home and no account. It is a stripped, statically linked Rust executable, about 230 MB,
  so the citations below quote its strings, not code. The npm `bin/devin.js` only launches the
  platform package's binary.
- **Upstream docs:** the same pages as the shipped ones, plus the stable changelog.

Most of this page was observed without an account: which files the binary reads and writes, found
with `strace`, `devin doctor`, `devin skills list`, `devin rules list` and unauthenticated
`devin acp` sessions. An ACP session loads every config source and logs what it loaded, with no
login needed. Each claim of the form "not read" below means a file was placed at that path and was
not loaded, not that a grep missed.

How rules decide a tool call was then observed with a signed-in agent on 2026-11-19: the same
binary in the same kind of container, a Devin Free account, the free `swe-2-flash-medium` model and
`devin -p`. See [Observed decisions](#observed-decisions).

## Destinations

The config directory is `$XDG_CONFIG_HOME/devin` when `XDG_CONFIG_HOME` is set, and
`~/.config/devin` otherwise. Observed in 3000.14.30821:

- `devin skills paths` with `HOME=/scratch` prints `/scratch/.config/devin/skills/<skill-name>/SKILL.md`;
  with `XDG_CONFIG_HOME=/xdg` it prints `/xdg/devin/skills/…`.
- `devin mcp add -s user …` with `XDG_CONFIG_HOME=/xdg` wrote `/xdg/devin/config.json` and
  `/xdg/devin/mcp_config.json`, and nothing under `~/.config`.
- With `XDG_CONFIG_HOME` set, skills, agents, `AGENTS.md` and `mcp_config.json` placed in
  `~/.config/devin` were not loaded. The variable replaces the default; it does not add to it.

So the variable is `XDG_CONFIG_HOME`, and it names a base directory to which Devin appends
`devin`, the same kind as OpenCode's. No variable names the directory itself: verified negative,
no `DEVIN_CONFIG` or `DEVIN_HOME` in the 3000.14.30821 binary, whose `DEVIN_*`, `WINDSURF_*`,
`COGNITION_*` and `XDG_*` names were enumerated by prefix. The `--config` flag moves only
`config.json`; `mcp_config.json` stays in the config directory.

| slice | global | project |
|---|---|---|
| permissions | `config.json` → `permissions` | `.devin/config.json` → `permissions` |
| permission mode, model | `config.json` → `permission_mode`, `agent.model` | none: user only |
| instructions | `AGENTS.md` in the config directory | `AGENTS.md` |
| MCP server definitions | `mcp_config.json` | `.devin/mcp_config.json` |
| hooks | `config.json` → `hooks` | `.devin/hooks.v1.json` (whole file) |
| skills | `skills/` in the config directory | `.devin/skills/` |
| agents (subagents) | `agents/` in the config directory | `.devin/agents/` |
| plugins | none: account or machine install, see [Plugins](#plugins) | `.devin/config.json` → `requiredPlugins` |

Every global path in the table follows `XDG_CONFIG_HOME`.

Each project file has a `.local.` sibling that Devin treats as personal, such as
`.devin/config.local.json` and `.devin/mcp_config.local.json`. The configuration-precedence page
says these "are automatically excluded from git via `.git/info/exclude`", and the binary did that:
`devin mcp add` at local scope appended `.devin/mcp_config.local.json` to `.git/info/exclude`.

Project config is narrow. The config-file reference says: "Only `permissions`,
`read_config_from`, and `hooks` are available in project configs." Plugin governance
(`requiredPlugins` and its siblings) is also read there. Everything else is user only.

Workspace trust is per path, in `~/.local/share/devin/cli/trusted_workspaces.json` (the binary's
source path `user-config/src/trusted_workspaces.rs`). `--respect-workspace-trust` "Defaults to
`true`", and "Non-interactive `--print` mode cannot show the workspace trust prompt, so it fails in
an untrusted directory." Observed in 3000.14.30821: `-p` in an untrusted directory exits 1 with
`Error: Refusing to run in an untrusted workspace`. With `{"trusted_paths":["/work/proj"]}` in that
file it runs. With `--respect-workspace-trust false` it runs too, and project permissions still
load.

### What Devin writes into `config.json` itself

`config.json` is shared with the CLI. Observed in 3000.14.30821:

- The first command that loads config (`skills list`, `mcp`, `doctor`, `acp`, but not `--version`
  or `--help`) creates `{"version": 1}`, and appends `"version": 1` to an existing file without it.
- Turning subagents off in a session appends `"subagents_enabled": false`.
- Both edits keep comments, unknown keys and key order. The file is JSON with comments.
- Changing the session mode did not write the file.
- The first signed-in run appended `"devin": {"org_id": …}`, `"shell": {"setup_complete": true}`
  and `"theme_mode": "dark"`, after the keys already there, leaving their formatting alone.

The docs list more writers that need an interactive session to observe: "Allow globally" approvals append to
`permissions`, `/shortcuts` saves to `keymap`, `Alt+P` in `/model` edits `agent.pinned_models`,
and toggling Local Hands bypass sets `local_tools_bypass_permissions`.

Unknown keys are accepted silently. A bad `permission_mode` value prints `Warning: Ignoring invalid
value for "permission_mode" … Using the default (null)`.

## Instructions

Devin calls instruction files rules. Always-on project files, at the workspace root and lazily in
subdirectories the agent touches: `AGENTS.md`, `AGENT.md`, `agents.md`, `AGENTS.local.md`,
`CLAUDE.md` and `.windsurfrules`. Rule folders: `.devin/rules/*.md`, `.windsurf/rules/*.md`,
`.cursor/rules/*.mdc`, plus `.devin/global_rules.md` and `.windsurf/global_rules.md`. Each was
loaded on its own in 3000.14.30821.

Global, in the config directory: `AGENTS.md`, `AGENT.md`, `agents.md` and `AGENTS.local.md`. Devin
also reads several global files belonging to other tools, all loaded in 3000.14.30821:
`~/.claude/CLAUDE.md`, `~/.devin/global_rules.md`, `~/.devin/rules/`, `~/.windsurf/rules/` and
`~/.cursor/rules/*.mdc`.

A file in `.devin/rules/` without frontmatter loads as `manual`, so it is not in the context
until someone invokes it. `trigger: always_on` makes it always on, but only as the first thing in
the file: in 3000.14.30821 `devin rules list` showed a rule starting with the frontmatter as
`always-on`, and the same frontmatter after an HTML comment as `manual`.

Verified negative in 3000.14.30821: `CLAUDE.local.md`, `.claude/rules/`, `.agents/AGENTS.md`,
`~/.config/devin/CLAUDE.md`, `~/.agents/AGENTS.md` and `~/.codex/AGENTS.md` are not read.

The rules page says of the global file: "Global rules work alongside project rules — both are
loaded and active at the same time." And of Claude's: "They can also be placed in the global config
directory to apply across all projects, except `CLAUDE.md` which is read globally from
`~/.claude/CLAUDE.md`."

`CLAUDE.md` and `~/.claude/CLAUDE.md` are read through the `claude` import, and `AGENTS.md`,
`AGENT.md`, `AGENTS.local.md` and `.windsurfrules` through `agents_standard`; see
[Third-party imports](#third-party-imports). A project `CLAUDE.md` holding only `@AGENTS.md` reaches
Devin as that literal line: no import syntax is documented for rules.

## Permissions

Rules are lists under `permissions.allow`, `permissions.ask` and `permissions.deny`, in
`config.json` at both scopes and in `.devin/config.local.json`. Resolution, from the permissions
page:

1. **Deny rules** — If matched, the action is blocked immediately.
2. **Ask rules** — If matched, you're prompted unless an equally or more specific command or MCP
   allow rule also matches.
3. **Allow rules** — If matched, the action proceeds without prompting.
4. **Default** — If no rule matches, you're prompted for approval.

"A deny rule always wins." Order within a list does not matter. The exception in step 2 is
deliberate: "an exact command is more specific than a prefix, and a longer prefix is more specific
than a shorter one", so `allow: ["Exec(git status)"]` carves `git status` out of `ask: ["exec"]`.
It "only applies within the same configuration level" and "does not apply to `Read()` or `Write()`
path globs".

Levels merge, highest first: organization, session grants, `.devin/config.local.json`,
`.devin/config.json`, then the user `config.json`.

### The shell matcher

`Exec(prefix)` "Matches commands that start with the given prefix", and "The prefix must match as a
complete word": `Exec(git)` matches `git status` but not `gitk`. There is no glob inside `Exec`.
The binary's matcher enum is `PermissionMatcher All Prefix Exact`, and the changelog for 3000.10.31
says "`Exec(*)` in a permission rule matches every command". So a command rule is all commands,
a word prefix, or an exact command.

The exact form is `ExecExact(command)`. No shipped page documents it: the binary holds the rule
string `ExecExact(` beside `Exec(`, and the permission prompt "now shows the exact rule you are
allowing". A signed-in run settled it (see [Observed decisions](#observed-decisions)):
`ExecExact(touch probe-out)` approved `touch probe-out`, and `ExecExact(touch)` did not. Malformed
rules produce no warning anywhere in 3000.14.30821.

The bare tool names `read`, `edit`, `grep`, `glob` and `exec` match a whole tool, and the changelog
adds `write` and `web_search`. So the catch-all is in the document: `"ask": ["exec"]` or
`"Exec(*)"`. The starting mode is a separate key, `permission_mode`, with values
`normal` (alias `auto`), `accept-edits`, `smart`, `dangerous` (aliases `yolo`, `bypass`) and
`autonomous`, which needs `--sandbox`. That list is the binary's own error text, and it matches the
commands reference.

### Paths, fetches and MCP tools

`Read(glob)` and `Write(glob)` take globs where `*` stays in one segment and `**` crosses them. A
relative glob is "resolved relative to your current working directory". `Fetch(pattern)` takes a
WHATWG URL pattern or `domain:host`. MCP tools are named `mcp__server__tool`, with `mcp__server__*`
and `mcp__*` for wider matches.

### Observed decisions

Each case gave Devin 3000.14.30821 one `-p` prompt to run `touch probe-out` in a fresh git
repository, or to read `secret.txt` with its read tool. Then the file system was checked. The
model was `swe-2-flash-medium`. A call that needs approval is rejected in `-p` mode, with
`warning: rejected a tool call that requires confirmation. Running in non-interactive mode.`

| user `config.json` | project `.devin/config.json` | result |
|---|---|---|
| none | none | rejected (needs approval) |
| allow `Exec(touch)` | none | ran |
| allow `Exec(tou)` | none | rejected: prefixes match whole words |
| allow `Exec(touch)`; command `touch probe-out && date > chained-out` | none | rejected: a prefix allow does not cover a chain |
| ask `Exec(touch)`, allow `Exec(touch probe-out)` | none | ran: the more specific allow wins |
| allow `ExecExact(touch probe-out)` | none | ran |
| allow `ExecExact(touch)` | none | rejected |
| `dangerous`, deny `Exec(touch)` | none | blocked, "denied by a deny rule in the user settings" |
| `dangerous`, deny `exec` | none | blocked |
| `dangerous`, ask `Exec(*)` | none | rejected: an ask beats the mode |
| `dangerous`, deny `Read(secret.txt)` | none | blocked |
| none | allow `Exec(touch)` | ran: project config loads |
| none | allow and deny `Exec(touch)` | blocked, "denied by a deny rule in the project settings" |
| none | deny `Read(secret.txt)` | blocked, although reads are auto-approved by default |
| deny `Exec(touch)` | allow `Exec(touch)` | blocked |
| allow `Exec(touch)` | deny `Exec(touch)` | **ran** |
| allow `Exec(touch)` | deny `exec` | **ran** |
| allow `Exec(touch)` | ask `Exec(touch)` | **ran** |
| allow `Exec(touch)` | deny `Exec(touch)` in `.devin/config.local.json` | **ran** |
| `dangerous` | deny `Exec(touch)` | **ran** |
| `dangerous` | deny `Read(secret.txt)` | **read the secret** |

The rows in bold contradict the configuration-precedence page: "A denial at a higher level cannot
be overridden by an allow at a lower level." In 3000.14.30821 a project deny or ask applies only
while the user level has no matching allow and is not in `dangerous` mode. A user deny wins
everywhere. So a project rule can tighten what the user level leaves to approval, but it cannot
take back what the user level allows.

## Settings

`config.json` holds the agent's settings, all user only: `agent.model`, `agent.subagent_model`,
`permission_mode`, `theme_mode`, `attribution`, `subagents_enabled`, `respect_gitignore`, `notify`,
`auto_update`, `proxy`, `sandbox`, `keymap` and `read_config_from`, among others. The binary's
`agent` block is `model`, `subagent_model`, `preferred_family_models`, `pinned_models`,
`show_history_on_continue`, `compaction_threshold_tokens`, `codex_tools` and `verbosity`.

Model names are Devin's: short family names like `opus`, `sonnet`, `swe`, `codex` and `gemini`
"always resolve to the latest version in that model family", and full slugs like `swe-1-6-fast`
work too. An unresolvable model is only logged. `--model` and `DEVIN_MODEL` override the file for
one launch, and `--permission-mode` and `DEVIN_PERMISSION_MODE` override the mode.

There is no effort key: verified negative in 3000.14.30821, whose subagent loader rejects `effort`,
`reasoning_effort` and `thinking`, and whose `agent` block has none. Effort is a session option,
`thought_level` over ACP, cycled with `Alt+T`.

## MCP

Server definitions are a `mcpServers` map in `mcp_config.json` (global) and
`.devin/mcp_config.json` (project), plus `.devin/mcp_config.local.json`. The dedicated files
arrived in v3000.3. Older versions kept the map in `config.json`, and newer ones migrate it out on
startup.

Keys: `command`, `args`, `env`, `cwd` for stdio; `url`, `transport` (`http` or `sse`), `headers`,
`oauthClientId`, `oauthClientSecret`, `oauthScopes`, `oauthResource` for remote servers; `disabled`
and `disabledTools` for either. The binary also accepts `serverUrl`, `urlOptions`, `tokenScope`,
`installationId` and `description`. `devin mcp add` writes `"transport"` on every entry, `"stdio"`
included, with two-space indentation:

    "ustdio": {"command": "npx", "args": ["-y", "some-server", "--flag"], "env": {"FOO": "bar"}, "transport": "stdio"}

`devin mcp add` also starts a stdio server to probe it, and posts analytics without an account.

Header values expand environment variables. The docs promise `${env:VAR}` only for the OAuth
fields, but a signed-in session in 3000.14.30821 sent `Bearer secret` to a local listener for both
`"Bearer ${env:TOK}"` and `"${TOK}"`, and sent `"Bearer $TOK"` and `"{env:TOK}"` as written.
`devin mcp list` and `devin mcp get` do not connect, so they show the unexpanded value.

## Skills

`SKILL.md` folders. Project roots loaded in 3000.14.30821: `.devin/skills`, `.agents/skills`,
`.claude/skills`, `.codex/skills`, `.github/skills`, `.kiro/skills`, `.cursor/skills`,
`.windsurf/skills`, `.codeium/skills` and the legacy `.cognition/skills`. Global: `skills/` in the
config directory, `~/.agents/skills`, `~/.claude/skills`, `~/.codex/skills`, `~/.copilot/skills`,
`~/.kiro/skills`, `~/.cursor/skills` and `~/.codeium/windsurf/skills`. Verified negative:
`~/.devin/skills`, `~/.config/opencode/skills` and `.opencode/skills` are not read.

So Devin reads the cross-harness `.agents/skills` at both scopes.

Only `<name>/SKILL.md` folders load. In 3000.14.30821, `devin skills list` left out a loose
`loose.md` at the top of `.devin/skills`, one at the top of `.agents/skills`, and a folder holding
Markdown but no `SKILL.md`.

Frontmatter in the binary's field list: `name`, `description`, `argument-hint`, `allowed-tools`,
`model`, `agent`, `permissions`, `disable-model-invocation` and `user-invocable`, plus `triggers`.
`disable-model-invocation: true` gave `triggers [user]` and `user-invocable: false` gave
`[model]`. Unknown keys are accepted silently, and a skill whose YAML does not parse is dropped
silently. `allowed-tools` auto-approves; it "Does **not** restrict tool availability".

## Agents

Devin calls them custom subagents: `<name>.md` or `<name>/AGENT.md` in `.devin/agents/`,
`.agents/agents/`, `agents/` in the config directory, and `~/.agents/agents/`. Verified negative in
3000.14.30821: `.claude/agents/` and `~/.claude/agents/` are not read; `devin doctor` probes the
six folders above and no others.

The format is Markdown with YAML frontmatter, and the frontmatter keys are `name`, `description`,
`model`, `allowed-tools` (alias `tools`; both at once is an error) and `max-nesting`, an integer.
`devin doctor` warns `CFG005 unsupported frontmatter key(s) ignored` for anything else, including
`effort`, `permissionMode`, `disallowedTools`, `color`, `skills` and `hooks`. The identity is
`name`, defaulting to the file or folder name; a profile named `subagent_explore` or
`subagent_general` is skipped. Unlike `allowed-tools` on a skill, on a subagent it restricts: "Restrict which tools the
subagent can use."

## Hooks

A native document in Claude's shape: event names mapping to `{matcher, hooks: [{type, command}]}`
groups, with `type` either `command` or `prompt`. The matcher is a regex against Devin's own tool
names: `exec`, `read`, `write`, `edit`, `apply_patch`, `grep`, `glob`, `webfetch`, `skill`,
`run_subagent` and `mcp__server__tool`, among others. They are not Claude's `Bash`, `Edit` or
`Read`. Hook entries also take `timeout`, `powershell`, `working_directory`, `show_output` and
`fail_closed`. Exit code 2 blocks; `DEVIN_PROJECT_DIR` is set to the project root.

Events that loaded in 3000.14.30821: `PreToolUse`, `PostToolUse`, `PermissionRequest`,
`UserPromptSubmit`, `Stop`, `PostCompaction`, `SessionStart` and `SessionEnd`, all documented, and
`ContextInput`, `PreCompact` and `PostCompact`, which are not. `Notification` and `SubagentStop`
are accepted and silently not loaded. `PostToolUseFailure` and `SubagentStart` are skipped with
`Skipping unsupported Claude hook event`.

Locations. Project: `.devin/hooks.v1.json`, where "the hooks object is the **entire file**", the
`hooks` key of `.devin/config.json` and `.devin/config.local.json`, and an undocumented
`.devin/hooks.json`. Global: the `hooks` key of `config.json` only. Verified negative:
`hooks.v1.json` in the config directory is not read. Both scopes also load Claude's hooks from
`.claude/settings.json`, `.claude/settings.local.json`, `~/.claude/settings.json` and
`~/.claude.json`, through the `claude` import. Whether Devin translates the matchers of the hooks
it imports from Claude was not established.

## Plugins

A plugin is a git source or folder with a `.devin-plugin/plugin.json` manifest, installed with
`devin plugins install owner/repo`. Installs are "at the **user** level", recorded "in your personal
manifest in Devin Cloud" or, with `--local`, on this machine; "Managing plugins requires being
signed in". No local file holds the user-level list: the CLI's own state is under
`~/.local/share/devin/cli/plugins_cache`.

A repository can require, endorse or forbid plugins with "the `requiredPlugins` /
`optionalPlugins` / `forbiddenPlugins` in a checkout's `.devin/config.json`", addressed by source
identity (`owner/repo` or a git URL), not by manifest name. None of this could be observed without
an account.

## Third-party imports

`read_config_from` in `config.json` (either scope, except `cloud`, which is user only) decides
which other tools' files Devin reads. Every key defaults to on: "If you don't explicitly configure
`read_config_from`, all imports are enabled by default." The binary's provider list is
`devin, agents_standard, cursor, windsurf, claude, opencode, zed, copilot, codex, kiro`, plus
`cloud`. Setting `claude` to `false` removed a `.claude/skills` skill in 3000.14.30821.

With the defaults, Devin reads much of what other harnesses' configs already contain:

- `claude`: `CLAUDE.md`, `~/.claude/CLAUDE.md`, `.claude/skills`, `~/.claude/skills`, hooks from
  Claude's settings files, and MCP servers from `.mcp.json`, `~/.claude.json` and Claude's settings.
- `codex`: `.codex/skills` and `~/.codex/skills`. "`CODEX_HOME` is not used."
- `cursor`: `.cursor/rules`, `.cursor/mcp.json` and, observed, `.cursor/skills` and
  `~/.cursor/skills`.
- `opencode`: MCP servers from `opencode.json` and `~/.config/opencode/opencode.json`.
- `agents_standard`: `AGENTS.md`, `AGENTS.local.md`, `AGENT.md` and `.windsurfrules`. Turning it
  off removes the project `AGENTS.md`.

Skills and agents in `.agents/` are read as Devin's own, not through an import.

## What loadout writes

Decided in [ADR 0058](../decisions/0058-devin-cli-is-a-supported-harness.md).

| slice | global | project | stealth source |
|---|---|---|---|
| permissions | the `permissions` key of `config.json` | the `permissions` key of `.devin/config.json`, other keys kept | `.devin/config.local.json` (whole) |
| settings | the leaves a `settings/<name>.json` fragment names, in `config.json` | `loadout/settings/devin.json`, the rest of `.devin/config.json` | none |
| instructions | `AGENTS.md` | the shared `AGENTS.md` | `.devin/rules/<name>.md`, starting with `trigger: always_on` |
| mcp | `mcp_config.json` | `.devin/mcp_config.json` | `.devin/mcp_config.local.json` (whole) |
| hooks | the `hooks` key of `config.json` | `.devin/hooks.v1.json` (whole) | none |
| skills | `skills/` | `.devin/skills/`, or the shared `.agents/skills/` copy | `.devin/skills/` |
| agents | `agents/` | `.devin/agents/` | none |
| module-config | the config directory | none | none |

There is no plugins slice: a user's plugins live in their Devin account.

**Permissions.** A plain entry becomes `Exec(entry)` and an exact one `ExecExact(command)`. A stated
`[shell] default` becomes `Exec(*)` at the end of the allow or ask list; `deny` becomes ask, since a
deny of every command would also block what the source allows, and a notice says so. Devin has no
glob, so an allow glob is dropped and a glob in ask or deny is widened to the words before its `*`,
each with a notice; one with its `*` inside the first word is refused in ask and deny. Because an
allow at least as specific as an ask overrides it in Devin, an allow inside an ask's range is
dropped, with a notice, and the commands it covered keep prompting. MCP rules are
`mcp__server__tool`.

**Project rules a user allow overrides.** Project sync reads the user's live `config.json`, for a
notice only, and reports each project deny or ask that one of its allows reaches, or every one of
them under `permission_mode` `dangerous` (aliases `yolo`, `bypass`). See
[Observed decisions](#observed-decisions). A stealth source is not checked.

**Settings and the CLI's own keys.** loadout owns `permissions`, `hooks` and the settings leaves of
`config.json`, recorded in an `.owned` file beside the fragment, and leaves the rest. Init adopts
everything a person wrote in `config.json` except `version`, `devin`, `shell` and `theme_mode`,
which the CLI writes on its first run. loadout reads the file as plain JSON, so a comment in it
makes sync refuse.

**Hooks.** Claude's tool names in a matcher become an anchored regex over Devin's: `Bash` is
`^exec$`, `Edit` is `^(edit|apply_patch)$`, and a name with no Devin form passes through, so
`mcp__github__.*` stays. `*` becomes `""`. A matcher on an event with no tool is dropped, an
`http` hook is left out, and both are reported. A hook naming a `$CLAUDE_*` variable is refused:
Devin sets `DEVIN_PROJECT_DIR`, and nothing shows it sets Claude's.

**Round trip.** On 2026-11-20, `loadout init global` adopted a signed-in container's
`config.json`, and `loadout sync --global` wrote a source allowing `touch`, denying `rm`, setting
`agent.model`, hooking Claude's `Bash` and adding a global instruction. In 3000.14.30821, with
`swe-2-flash-medium`, one `devin -p` ran `touch`, refused `rm` "by a deny rule in the permission
settings", fired the hook through its `^exec$` matcher and quoted the instruction. The CLI's own
keys were still in `config.json`, and `loadout check --global` found no drift afterwards.

**Third-party imports.** With Claude declared too, Devin also reads Claude's
`~/.claude/CLAUDE.md`, `.claude/skills` and hooks, so it sees those twice. Sync says so until a Devin
settings fragment sets `{"read_config_from": {"claude": false}}`, which also stops Devin reading the
project `CLAUDE.md`. loadout does not set it, since the import is the user's choice.
