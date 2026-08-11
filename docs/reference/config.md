# Where each harness keeps each kind of configuration

Agent configuration divides into seven kinds — **settings, instructions, permissions, hooks,
mcp, plugins, skills**. Each harness stores them in a different place, and the boundaries
between them differ too: what is one file on one harness is three on another.

**Antigravity is recorded here but is not a target** — loadout emits nothing for it, per
[0012](../decisions/0012-antigravity-is-dropped-until-it-matures.md). Its column stays because
the verified negatives below are precisely what a decision to re-add would have to overturn.

This page records *where things are*. Matcher semantics and rendering behaviour live in the
per-harness pages; [README.md](README.md) indexes those.

**Verified 2026-08-09**, hooks section updated 2026-08-11, against Claude Code 2.1.226, codex-cli 0.147.0, OpenCode 1.18.15, Pi
0.84.1 and Antigravity (`agy`) 1.1.11, by inspecting the installed binaries and shipped docs —
not by reading upstream documentation. Method per harness: `strings` over the Claude, Codex,
OpenCode and `agy` binaries; the JavaScript bundle and shipped `docs/` for Pi.

Two caveats on that. Pi's evidence comes from the **0.80.10** tree on disk while the running
binary reports 0.84.1, so Pi's rows are one minor version behind. And a blank cell means **not
found**, not "does not exist" — per [AGENTS.md](../../AGENTS.md), absence of a Claude-style
filename in another harness proves nothing.

## Summary

| | Claude | Codex | OpenCode | Pi | Antigravity |
|---|---|---|---|---|---|
| settings | ✓ | ✓ | ✓ | ✓ | ✓ |
| instructions | ✓ | ✓ | ✓ | ✓ | ✓ |
| permissions | ✓ | ✓ | ✓ | ✓ | ✓ |
| hooks | ✓ declarative | ✓ declarative | ✓ **in code** | ✓ **in code** | ✓ declarative |
| mcp | ✓ CLI only | ✓ | ✓ | ✓ | ✓ |
| plugins | ✓ | ✓ | ✓ npm | ✓ | ✓ |
| skills | ✓ | ✓ | ✓ | ✓ | ✓ project only |

Four structural facts fall out of the detail below, and each matters more than any individual
path.

**There is a cross-harness `.agents/` convention, and Claude is the only one not in it.** See
the section below — it is the single most consequential finding here, because for some slices
one write covers three harnesses.

**Slice boundaries are not file boundaries.** Claude puts settings, permissions and hooks in one
`settings.json`. OpenCode puts settings, permissions, instructions and mcp in one
`opencode.json`. Codex splits settings (`config.toml`), permissions (`rules/`) and hooks
(`hooks.json`) into three. Pi splits them four ways. Any design assuming one slice ↔ one file is
wrong on three of five harnesses.

**All four supported harnesses have hooks; two declare them and two register them in code.**
Claude and Codex take a JSON document of events. OpenCode and Pi have no hooks *file*, which is
not the same as having no hooks — both expose a documented event API that a TypeScript
plugin/extension subscribes to, and Pi's is larger than Claude's.

**Only Claude and OpenCode have a machine-wide managed tier.** Codex, Pi and Antigravity have
none.

## The `.agents/` convention

Four of the five harnesses read a shared, harness-neutral directory — `~/.agents/` globally and
`.agents/` in a project. Coverage is per slice, not all-or-nothing:

| slice | Claude | Codex | OpenCode | Pi | Antigravity |
|---|---|---|---|---|---|
| skills | | | `~/.agents/skills/` | `~/.agents/skills/`, `.agents/skills/` | `.agents/skills/`, `.agents/skills.json` |
| plugins | | `~/.agents/plugins/`, `.agents/plugins/` | | | `.agents/plugins/` |
| instructions | | | | | `.agents/rules/` |
| hooks | | | | | `.agents/hooks.json` |

**Claude reads nothing under `.agents/`** — zero occurrences in the 2.1.226 bundle. Antigravity
is the broadest adopter, with `rules/`, `hooks.json`, `plugins/`, `skills/`, `skills.json` and
`agents/` beneath it.

The practical consequence for the skills slice: writing `~/.agents/skills/<name>/` once serves
OpenCode and Pi, and `.agents/skills/` serves Antigravity at project scope — leaving only Claude
and Codex needing their own directories. Pi can additionally be *configured* to read
`~/.claude/skills` and `~/.codex/skills` directly, so a Pi user who already has Claude skills
need not copy them.

This is the clearest illustration of why the never-generalise-from-Claude rule exists. Reasoning
from Claude's layout, this convention is invisible.

## settings

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/settings.json` | `.claude/settings.json`, `.claude/settings.local.json` |
| Codex | `~/.codex/config.toml` | `.codex/config.toml` |
| OpenCode | `~/.config/opencode/opencode.json` | `opencode.json` at repo root |
| Pi | `~/.pi/agent/settings.json` | `.pi/settings.json` |
| Antigravity | `~/.gemini/antigravity-cli/settings.json` | |

Observed top-level keys, to show how little the vocabularies overlap:

- **Claude** — `$schema`, `cleanupPeriodDays`, `skillListingBudgetFraction`, `env`,
  `attribution`, `permissions`, `model`, `hooks`, `statusLine`, `enabledPlugins`, `sandbox`,
  `effortLevel`, `awaySummaryEnabled`, `autoMemoryEnabled`, `skipAutoPermissionPrompt`,
  `skipWorkflowUsageWarning`
- **OpenCode** — `$schema`, `model`, `provider`, `permission`, `mcp`
- **Pi** — `lastChangelogVersion`, `theme`, `defaultProvider`, `defaultModel`,
  `defaultThinkingLevel`, `enabledModels`, `packages`
- **Antigravity** — `~/.gemini/antigravity-cli/settings.json` held only `permissions`

`model` is the only key name shared by more than one harness, and its values are drawn from
different vocabularies (`opus` vs `openrouter/deepseek/deepseek-v4-flash-0731`), so even that one
does not translate.

`~/.gemini/settings.json` also exists but was **empty** on this machine, so its schema and its
relationship to the `antigravity-cli/` one are unverified.

## instructions

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/CLAUDE.md` | `CLAUDE.md`, `CLAUDE.local.md` |
| Codex | `~/.codex/AGENTS.md` | `AGENTS.md` |
| OpenCode | `instructions` key in `opencode.json` | `AGENTS.md`; also `CLAUDE.md`, `opencode.md` |
| Pi | `~/.pi/agent/AGENTS.md` | `AGENTS.md` |
| Antigravity | `~/.gemini/GEMINI.md` | `.agents/rules/` |

OpenCode's `instructions` key is **confirmed** — the bundle carries the example
`"instructions": ["AGENTS.md", "docs/style.md"]`, so it is a list of paths rather than a single
file, and `AGENTS.md`, `CLAUDE.md` and `opencode.md` are all recognised filenames.

The three `AGENTS.md`-family documents are typically byte-identical, which is why extraction can
collapse them into one fragment by exact comparison rather than judgement.

## permissions

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/settings.json` → `permissions` | `.claude/settings.json`, `.claude/settings.local.json` |
| Codex | `~/.codex/rules/*.rules` (directory, read whole) | `.codex/rules/*.rules` |
| OpenCode | `~/.config/opencode/opencode.json` → `permission` | `opencode.json` → `permission` |
| Pi | `~/.pi/agent/extensions/pi-permission-system/config.json` | `.pi/extensions/pi-permission-system/config.json` |
| Antigravity | `~/.gemini/antigravity-cli/settings.json` → `permissions` | none known |

The only slice loadout renders today. Matcher semantics, pattern shapes and resolution order are
in the per-harness pages — do not re-derive them from these paths.

Codex and Pi are the two that give permissions their own file. The other three share a file with
settings, which is why those targets need a `base` document and the rest do not.

## hooks

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/settings.json` → `hooks` | `.claude/settings.json` → `hooks` |
| Codex | `~/.codex/hooks.json` → `hooks` | |
| OpenCode | **in code** — `~/.config/opencode/plugins/*.ts` | |
| Pi | **in code** — `~/.pi/agent/extensions/*.ts` | |
| Antigravity | `~/.gemini/config/hooks.json` | `.agents/hooks.json` |

**Claude and Codex share an event vocabulary and an entry shape.** Both are a map of event name
→ list of entries, each `{matcher?, hooks: [{type, command, timeout}]}`. Observed events:

- **Claude, from the 2.1.226 binary — 16 events:** `Notification`, `PermissionRequest`,
  `PostCompact`, `PostToolUse`, `PostToolUseFailure`, `PreCompact`, `PreToolUse`, `SessionEnd`,
  `SessionStart`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `UserPromptSubmit`,
  `WorktreeCreate`, `WorktreeRemove`
- **Codex, from the 0.147.0 binary — 11 events:** `PermissionRequest`, `PostCompact`,
  `PostToolUse`, `PreCompact`, `PreToolUse`, `SessionEnd`, `SessionStart`, `Stop`,
  `SubagentStart`, `SubagentStop`, `UserPromptSubmit`

**Codex's set is a strict subset of Claude's** — 11 shared, 5 Claude-only, none Codex-only. And
Codex carries Claude's hook *I/O* vocabulary too (`hookSpecificOutput`, `permissionDecision`,
`permissionDecisionReason`, `updatedInput`, `additionalContext`, `hook_event_name`, `tool_input`),
and honours exit code 2. Codex implements Claude's hook protocol rather than a parallel one.

Only 11 of Claude's 16 and 9 of Codex's 11 are configured on this machine; `WorktreeCreate`
appears in no config here at all. **Configuration is a lower bound on capability, always.**

`matcher` appears only on Claude: every entry in `~/.codex/hooks.json` is `{hooks: [...]}` alone
— though that is configuration again, so it is not proof Codex lacks the concept.

On Claude, 6 of the 11 configured event lists contain entries with **no `matcher` key**, so
`matcher` cannot serve as a merge key for those lists.

### OpenCode and Pi register hooks in code

Neither has a hooks *file*; both have a documented event API. This is a different mechanism, not
a missing one — and Pi's surface is larger than Claude's.

- **Pi** — `docs/extensions.md` (2,336 lines) documents ~24 events across eight groups:
  `project_trust`, `resources_discover`, `session_start`, `session_info_changed`,
  `session_before_switch`, `session_before_fork`, `session_before_compact`, `session_before_tree`,
  `session_shutdown`, `before_agent_start`, `agent_start`, `turn_start`, `message_start`,
  `tool_execution_start`, `context`, `before_provider_headers`, `before_provider_request`,
  `after_provider_response`, `model_select`, `thinking_level_select`, `tool_call`, `tool_result`,
  `user_bash`, `input`, `render`. Pi's own docs call these hooks.
- **OpenCode** — a plugin returns handlers keyed `tool.execute.before`, `tool.execute.after`,
  `chat.message`, `chat.params`, `permission.ask`, `auth`, plus a catch-all `event` stream.

Several Pi events have no Claude analogue in either direction — `before_provider_request` can
rewrite the system prompt, `render` transforms display — so this is not a subset relationship.

## mcp

| harness | global | project |
|---|---|---|
| Claude | `claude mcp add-json` CLI; state in `~/.claude.json` | `.mcp.json` |
| Codex | `~/.codex/config.toml` → `[mcp_servers.*]` | `.codex/config.toml` |
| OpenCode | `~/.config/opencode/opencode.json` → `mcp` | `opencode.json` → `mcp` |
| Pi | `~/.pi/agent/mcp.json` (+ shared paths below) | `.pi/mcp.json`, `.mcp.json` |
| Antigravity | `~/.gemini/config/mcp_config.json` | |

**Pi core has no MCP at all.** Its docs say so outright — "It intentionally does not include
built-in MCP, sub-agents, permission popups, plan mode, to-dos, or background bash." MCP arrives
via the `pi-mcp-adapter` package, which supports native `mcpServers` definitions and reads a
layered set of paths, several of them **tool-agnostic**:

| file | purpose |
|---|---|
| `~/.config/mcp/mcp.json` | user-global shared |
| `~/.agents/mcp.json`, `~/.agents/mcp/mcp.json` | user-global tool-agnostic |
| `.mcp.json` | project-local shared — **the same file Claude reads** |
| `~/.pi/agent/mcp.json` | Pi global override and compatibility imports |
| `.pi/mcp.json` | Pi project override |

`{"imports": ["claude-code"]}` in `~/.pi/agent/mcp.json` is a **compatibility import**, not the
only option — `~/ac` generated one until 2026-08-11 and now emits real `mcpServers` instead: `pi-mcp-adapter init` scans for host-specific
configs and adds them. The adapter also loads servers from Agent Plugins 1.0 packages
(<https://agent-plugins.org/>), prefixed `<plugin>__<server>`.

**Claude is the exception and the only one with no writable global file.** `~/.claude.json` mixes
MCP server definitions with session state and per-project history, and `~/.claude/settings.json`
has no `mcpServers` key at all. `~/ac/mcp/sync.py` therefore generates an input file and feeds it
through `claude mcp add-json` rather than writing config directly — a harness-owned mutation
path, not a file loadout can render, which collides with
[0004](../decisions/0004-loadout-is-render-only.md).

## plugins

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/plugins/`, enabled via `settings.json` → `enabledPlugins` | |
| Codex | `~/.codex/plugins/`, `~/.agents/plugins/` | `.agents/plugins/` |
| OpenCode | `~/.config/opencode/plugins/*.ts`, plus npm `package.json` | |
| Pi | `~/.pi/agent/settings.json` → `packages`; `./extensions` | |
| Antigravity | `~/.gemini/config/plugins/` | `.agents/plugins/` |

Plugins are consistently **two things**: content on disk, and a declaration that it is on.

**Claude and Codex converge**: both address a plugin as `<name>@<marketplace>` and both need the
marketplace registered separately.

- Claude — `settings.json` → `"enabledPlugins": {"superpowers@claude-plugins-official": true}`,
  with marketplaces in `~/.claude/plugins/known_marketplaces.json`, a harness-managed file
  carrying `lastUpdated` and `installLocation`.
- Codex — `config.toml` → `[plugins."nono@nolabs-ai"] enabled = true`, with
  `[marketplaces.nolabs-ai] source_type = "local"` in the *same* file.

**Pi and OpenCode do not.** Pi has no marketplace concept: `packages` references a source
directly (`npm:`, `git:`, or a path), as a string or an object. OpenCode has no enablement list
at all — a plugin is on because its `.ts` file exists, with dependencies in an npm
`package.json`.

## skills

| harness | global | project |
|---|---|---|
| Claude | `~/.claude/skills/<name>/` | `.claude/skills/<name>/` |
| Codex | `~/.codex/skills/<name>/` | |
| OpenCode | `~/.config/opencode/skills/<name>/`, `~/.agents/skills/` | |
| Pi | `~/.pi/agent/skills/`, `~/.agents/skills/` | `.pi/skills/`, `.agents/skills/` |
| Antigravity | **none** | `.agents/skills/`, `.agents/skills.json` |

**Antigravity has no global skills directory** — a verified negative rather than a blank. The
1.1.11 binary contains no home-relative skills path of any kind: no `~/.agents/`, no
`~/.gemini/**/skills`. Every skills path in it is project-relative `.agents/skills/` or its own
bundled `assets/external/skills/` and `antigravity-cli/builtin/skills/`.

Every other harness has its own skills directory at global scope, so `~/.agents/skills/` is an
*additional* location for OpenCode and Pi rather than the only one. At project scope the reverse
holds: `.agents/skills/` is Antigravity's only option, and Pi — which also reads it — has
`.pi/skills/` as an alternative.

All five have a skills mechanism. An earlier draft of this page recorded Pi and Antigravity as
having none, on the evidence that no skills were *installed* for them on this machine — a
mistake, and exactly the inference the never-generalise rule forbids.

Pi's shipped `docs/skills.md` is the most explicit of the five and worth reading directly. Two
details from it that no other harness documents:

- Discovery differs by directory. In `~/.pi/agent/skills/` and `.pi/skills/`, a bare root `.md`
  file is discovered as a skill; in `~/.agents/skills/` and `.agents/skills/` root `.md` files
  are **ignored** and each skill must be a directory.
- Project `.agents/skills/` is searched in `cwd` **and every ancestor** up to the git repo root,
  or the filesystem root outside a repo.

Pi also accepts extra skill directories in settings, and documents `~/.claude/skills` and
`~/.codex/skills` as the intended values.

## Managed / enterprise tier

| harness | path | evidence |
|---|---|---|
| Claude | `/Library/Application Support/ClaudeCode/` | 103 `managed-settings` references |
| OpenCode | `/Library/Application Support/opencode/` | path present in bundle |
| Codex | none | 0 references, no `/Library` path |
| Pi | none | 0 references |
| Antigravity | none | 0 references |

Only Claude has an explicit managed-settings mechanism. OpenCode has the directory but no
`managed-settings`-style naming, so what it reads there is unverified.

## Not verified

Each is a real gap, not a judgement that the feature is absent:

- `~/.gemini/settings.json` schema — the file was empty on this machine, and its relationship to
  `~/.gemini/antigravity-cli/settings.json` is unknown.
- Plugin **enablement** mechanism for Codex and Antigravity. Both have marketplaces; neither
  revealed how a plugin is switched on.
- What OpenCode reads from `/Library/Application Support/opencode/`.
- Project-scope paths for mcp on Pi and Antigravity, and for hooks on Codex, OpenCode and Pi.
- Whether Codex's `~/.agents/plugins/` is read at global scope or only resolved as a marketplace
  script path.
- Pi's rows reflect 0.80.10 on disk; the running binary reports 0.84.1.
