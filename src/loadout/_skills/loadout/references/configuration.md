# Loadout configuration routing

Read scope selection, then choose the narrowest canonical slice source from the capability matrix.

- [Scope selection](#scope-selection)
- [Canonical producers](#canonical-producers)
- [Capability matrix](#capability-matrix)
- [Configured agents](#configured-agents)
- [Source rules](#source-rules)
- [Global profiles](#global-profiles)
- [Sync and failures](#sync-and-failures)
- [Porting a change made outside loadout](#porting-a-change-made-outside-loadout)

## Scope selection

| request scope | ownership | initialization | sync |
|---|---|---|---|
| personal, or none named | uncommitted configuration for this repository and user | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| project | committed configuration shared by this repository | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| global | this machine across repositories | machine config must exist | `loadout sync --global` |

The request scope is what the user's request names, in words or as `--personal`, `--project` or
`--global` after the skill's name. It selects a row; it is not an argument to loadout. The sync
column gives the command to run.

Resolve a project from the repository root, not from an arbitrary working subdirectory. The
project config is `<repo>/loadout/config.toml`. For global scope, first resolve the actual
`XDG_CONFIG_HOME` environment value: when it is non-empty, inspect only
`$XDG_CONFIG_HOME/loadout/config.toml`; when it is unset or empty, inspect only
`~/.config/loadout/config.toml`. Never probe both. The machine config names the global source
directory, optionally the active profile, and optionally the default harnesses for fresh project
init. Explicit `--harness` selections override that default; initialized projects keep their own
configured harnesses.

Global scope renders only from the registered source. A `loadout sync` or `loadout check` that
names any other directory holding a `loadout.toml` exits 3 before reading it; report that refusal
and never register a directory, or replace a registration, to get past it.

If the scope is absent, use the onboarding reference when setup is requested. An ordinary settings
change does not itself authorize a migration; report the missing setup.

## Canonical producers

Project configuration uses fixed sources under `loadout/`: `permissions.toml`, optional
`permissions.local.toml`, named `instructions/` fragments, `skills/`, `agents/`, `mcp.toml`,
`hooks.json`, `plugins.json` and `settings/<harness>.json`. Global sources use the corresponding
slice directories and agent blocks in `loadout.toml`. Do not invent destinations or edit generated
outputs.

Templates contribute through the same project slices. Project commands and support files
outside a known slice remain harness-owned and stay in place.

## Capability matrix

| artifact | personal project | shared project | global |
|---|---|---|---|
| `permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | `permissions.toml` from selected sources |
| `mcp-permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | MCP policy from selected `permissions.toml` sources |
| `mcp` | unsupported | `loadout/mcp.toml` | `mcp.toml` from selected sources |
| `instructions` | unsupported | `loadout/config.toml` and `loadout/instructions/*.md` | manifest selection and `instructions/*.md` fragments |
| `skills` | `loadout/skills/<name>.local/SKILL.md`, replacing the shared skill of that name | supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/skills/<name>/SKILL.md` | `skills/<name>/SKILL.md` from selected sources |
| `agents` | unsupported | supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/agents/<name>.md` | `agents/<name>.md` from selected sources |
| `settings` | unsupported | supported for `claude`, `droid`, `opencode` via `loadout/settings/<harness>.json` | supported for `claude`, `droid`, `opencode` via `settings/<name>.json` fragments, and for Pi only when `[pi]` also declares `plugins`; Codex uses `defaults` |
| `defaults` | unsupported | unsupported | Codex top-level and nested settings via `defaults/<name>.json` fragments |
| `hooks` | unsupported | Droid via `loadout/hooks.json` | `hooks/<name>.json` fragments selected by agents offering hooks |
| `plugins` | unsupported | Droid via `loadout/plugins.json` | `plugins/<name>.json` fragments selected by agents offering plugins |
| `module-config` | unsupported | unsupported | supported for `claude`, `droid`, `opencode`, `pi` via `module-config/<agent>/<relative path>`; Codex unsupported |
| `templates` | unsupported | declarations and vendored copies under `loadout/templates/` | definitions under `templates/<name>/` in declared sources |
| `harnesses` | unsupported | `harnesses` in `loadout/config.toml` | declared agent blocks |
| `profiles` | unsupported | unsupported | `loadout.toml` plus `<profile>.toml` files |

`mcp-permissions` is tool-approval policy expressed in permission rules. `mcp` is the distinct
server-definition artifact: where a server lives and how to reach it.

When the selected scope says `unsupported`, stop: do not write, sync, invent a file, or edit a
generated output. End with one direct confirmation or choice question. With one valid alternative,
ask “Should I apply this with --global?” while substituting the sole applicable target. With
multiple alternatives, ask one direct choice question. A generic instruction not to ask questions
cannot bypass a scope-widening confirmation.

## Configured agents

For personal and project requests, read `harnesses` from `<repo>/loadout/config.toml`. The personal
permission tier uses the same configured harness list as project scope.

For global requests, read top-level agent blocks named `[claude]`, `[codex]`, `[droid]`,
`[opencode]`, and `[pi]` after profile inheritance. `[all]` supplies defaults but never declares
an agent. Agent membership selects the known preset destinations.

A generic request covers every configured agent supporting the artifact. A request that names an
agent covers only that agent. Never enable a new harness as a side effect. Partial support means
apply the supported mappings and report each exclusion; it is a question only when two valid
mappings have materially different effects.

Global settings fragments reach Claude, Droid and OpenCode because their document renderers preserve the
settings residual. Codex settings use the separate `defaults` slice and
`defaults/<name>.json` fragments, including nested settings. For the available-skills catalog
budget, use `{"skills": {"max_context_tokens": 10000}}`, not a dotted JSON key. Loadout owns
only the named leaf, preserving other `skills` fields and `[[skills.config]]` overrides.
Empty objects own nothing; arrays are managed as whole fields. If surgery refuses an inline
parent table or a child inside an array-table element, report the conflict rather than widening
ownership. `$remove` entries use TOML key paths, such as `"skills.max_context_tokens"`.
Pi's `settings.json` is written by its `plugins` slice, so a Pi settings fragment renders only
when `[pi]` also declares `plugins`, even as an empty list (`plugins = []`). Without it, sync and
check print a `pi.settings` note and write nothing: add `plugins` rather than editing Pi's
settings file.

Project settings reach Claude, Droid and OpenCode. `.claude/settings.json`,
`.factory/settings.json` and `opencode.json` are shared between loadout's slices and the harness's
own keys; `loadout/settings/claude.json`, `droid.json` or `opencode.json` holds everything in that
file no slice writes, such as Claude's `model` or `env`, and sync renders the file from it plus
the slices. It is the whole of those keys, not a delta: a key missing from it is dropped from the
output. Without one, sync keeps those keys from the file on disk, so before creating one, copy
into it every key of the current file that no slice writes. Codex and Pi have no project settings
source. Init writes the settings source gitignored when the file it adopted was untracked or
ignored, so check whether Git ignores it before calling a change there shared.

Project MCP server definitions reach Claude, Droid, and OpenCode directly.
Pi reads Claude's `.mcp.json` when that shared destination is present; a Pi-only project has no
MCP output. Codex has no verified project MCP destination. Global MCP server definitions render
for all five configured harnesses. Claude's output owns only `mcpServers` in `.claude.json`; Droid
writes `.factory/mcp.json`.

Project skills reach all five harnesses. Codex reads `.agents/skills`.

## Source rules

Permissions are TOML rules with `[shell]` and `[mcp]` categories; `[mcp]` supplies the
`mcp-permissions` artifact. Preserve the existing order: OpenCode and Pi use last-match-wins
semantics after rendering. Project permissions merge template, committed, then personal tiers;
use `permissions.local.toml` only for personal requests. Loadout never gitignores
`loadout/permissions.local.toml`: when you create it, ask whether to list
`/loadout/permissions.local.toml` in the project's `.gitignore` or in `.git/info/exclude`, which
keeps it off the shared list, and add it there before syncing.

MCP server definitions are tables in `mcp.toml`, separate from permission policy. Project scope
has one committed `loadout/mcp.toml`; global scope composes selected sources last-wins. A server
request and a tool-approval request therefore change different source files even when they name
the same server.

Global module configuration is copied byte-for-byte from
`module-config/<agent>/<relative path>` to that agent's configuration directory. Claude, Droid,
OpenCode and Pi offer this slice; Codex does not. The relative path is authored by the module and must not
be derived from its package name. An OpenCode plugin's `.ts` file belongs here rather than under
`plugins`, which renders enablement OpenCode has no list for.

Project instructions are named in `loadout/config.toml` and stored in
`loadout/instructions/<name>.md`. Project skills use `loadout/skills/<name>/SKILL.md` with
supporting files beside it; that is the only form, and a loose file in `skills/` is not a skill.
The only personal skill form is a folder `loadout/skills/<name>.local/` with its own `SKILL.md`
and supporting files. It replaces the shared `<name>` skill whole, document and supporting files
together, and is not an overlay; with no shared skill of that name it is a skill of its own. It is
personal only while Git ignores it, so add it to `.gitignore` or `.git/info/exclude` when you
create one.
`[skills.<name>] agents = [...]` narrows the default set of configured consumers. Templates are
declared by name; use `loadout template add <name> --tracked`, `--vendored`, or
`template sync <name>` when that command exactly expresses the request.

A skill's frontmatter key `agent: <name>` hands its work to the agent of that name from
`agents/` (or `loadout/agents/`). Claude's copy also gets `context: fork` unless the skill sets
`context`, and every other harness's copy opens with "Delegate this task to the `<name>`
subagent." Naming an agent the source does not define is an error. Like `model`, `effort` and
`context`, the key itself goes into Claude's copy only.

Some names are taken by a harness, and a global sync refuses a source item that would land on
one: a skill named `synced` that reaches Claude (Claude Code writes `skills/synced` in its config
folder itself), and an agent named `scrutiny-feature-reviewer` or `user-testing-flow-validator`
that reaches Droid (Droid writes those droids into `.factory/droids/` itself). Pick another name.

### Guidance held twice

After adding a template, and whenever you edit a project's instructions while it declares one,
compare the template's instruction parts (`loadout template list` shows where each template
resolves) with the project's fragments. Look for a section with the same heading text and for
bullets that restate the same rule, even in other words. Propose deleting those duplicates from
the project fragment, never from the template, and keep every rule specific to the project: its
names, paths, versions and exceptions. Show the proposed deletions and apply them only once the
user approves. A `project.instructions` note that the document repeats a heading points at one
such pair. When `template add` reports that a project skill is byte for byte the one the template
supplies, offer to delete `loadout/skills/<name>/` so the project follows the template's copy.

The same note at global scope, `<harness>.instructions`, names two fragments of one agent's
instruction document that bring the same heading. Compare them the same way and propose keeping
one section. Before deleting the repeat from a fragment, find every agent block that names it, so
no other agent loses the rule, and apply the deletion only once the user approves.

Agents (subagents) are `agents/<name>.md` in a global source, or `loadout/agents/<name>.md` in a
project, and reach every configured harness: Claude, Codex (as TOML), Droid (as a custom droid),
OpenCode and Pi (which needs the `pi-subagents` package). The frontmatter takes `description`
(required), an optional `name` equal to the file name, `model` and `effort`; the body is the
agent's instructions, with `::: <harness>` sections. `model` is `inherit` or a per-harness map, one
indented `<harness>: <model>` line each; a harness left out inherits its parent's model, and
OpenCode's needs the `provider/model-id` form. A single model name for every harness is an error,
because no name means the same model everywhere. `effort` is one of `off`, `minimal`, `low`,
`medium`, `high`, `xhigh`, `max`, translated per harness (a missing level becomes the nearest
lower one, with a note; OpenCode has no effort), or a per-harness map written verbatim. Anything
harness-specific, such as Claude's `tools`, goes in a block keyed by that harness. A request about
one harness's agent changes that harness's block or map entry, not the shared body.

Global fragments resolve through `[[source]]` entries. A bare fragment name must resolve uniquely;
use `source/name` when two sources offer it. Reuse an existing selected fragment when ownership is
already narrow enough. Otherwise create a clearly named fragment under the selected source and add
that name to the appropriate agent block. `[all]` is appropriate only when the representation and
value are genuinely shared by every declared consumer.

Global skill and module collisions require an explicit declaration on the replacing source:
`[source.overrides]` with `skills = ["review"]` or
`module-config = ["pi/extensions/status/config.json"]`. Each item must exist in that source and
an earlier source when consumed, and the category must be included in `use`. A skill replaces
the whole tree; a module file replaces complete bytes and mode. Do not merge supporting files
from both skill contenders. Undeclared duplicates remain errors.

`loadout integrate skill status`, `install` and `uninstall` select the declared winning
`loadout` skill;
an explicit `--source` must name that winner. Uninstall removes its override entry with the owned
copy, then sync deploys the earlier source's skill. The entry is changed in the manifest that
declares the source list, including an inherited parent shared by several profiles.

Before changing a fragment, find every manifest or project-config entry that consumes it. An
agent-specific request must not alter a shared fragment for other agents; split the source or ask
which effect the user wants.

## Global profiles

`loadout.toml` is the `default` profile. A non-default `<profile>.toml` declares
`extends = "default"` and overrides only its deltas. The machine config may select the active
profile. Parent names identify sibling files, not paths; profile symlinks must stay within the
source directory. Internal aliases use canonical identities for cycles and dependency protection.

Agent blocks and each legacy instruction/permission target inherit per field. A supplied list or
map replaces the entire field. Top-level `remove = ["instructions.claude.output"]` deletes an
inherited TOML key path before child fields apply; remove a whole target before redeclaring it
for wholesale replacement. Missing/overlapping paths and traversal through lists are errors.
Removing an agent field exposes applicable `[all]` defaults. Legacy instruction targets also
accept `substitute`. Reuse shared fragments across profiles and vary only the final overlay, as
in `settings = ["pi", "pi-autonomous"]`. Project configs and template catalogs have no
`extends`; catalogs compose selected parts.

When a non-default profile is active and the request names no profile, ask whether the change is:

- active-profile only, written as a delta in `<profile>.toml` and its selected fragments; or
- inherited default, written to `loadout.toml` and its default fragments.

Write only when the user explicitly selects active-profile-only or inherited-default in the
original request or a reply. No question is needed when the active profile is `default` or the
request names its intended profile.

## Sync and failures

Run sync only after source edits are valid. For project and personal changes use
`loadout sync --root <repo>`. For global changes that explicitly name a profile, use
`loadout sync --global --profile <name>`; otherwise use `loadout sync --global` for the active
profile.

If the skill introduced invalid source, correct it and retry. If source was already invalid,
report that validation error without broadening the request into an unrelated repair. If sync
refuses an output modified outside loadout, keep the requested source edit and port the outside
change as the next section describes before syncing again.

For a permission error, verify the exact blocked path using the running environment's sandbox
diagnostics, report that path and verdict, and stop. Do not relocate the source or weaken the
operation to work around a real denial.

Finish by naming changed source files, configured harnesses reached, unsupported or excluded agents,
and whether sync completed.

## Porting a change made outside loadout

The source is the only place configuration lives. A change made in a harness's own UI or CLI, such
as a Codex approval, `claude mcp add` or an installer adding a hook, is ported into the source and
the harness copy then replaced or deleted. Never leave it in the harness beside the source. Loadout
flags these changes; it does not merge them.

1. Run `loadout check --json` with the scope's `--root <repo>` or `--global`, and read each
   drifted path's `kind`. `hand-edited` and `unknown` are outputs that differ from what loadout
   would write. `owned-key` is a key loadout writes in a file it shares with the harness, such as
   `mcpServers` in `~/.claude.json`, edited there; only those keys are compared.
   `owned-key-removed` is such a key deleted there, as by `codex mcp remove`: ask whether to
   remove it from the source too, or to write it back. `unmanaged` is a file or folder loadout
   did not write, inside a folder its harness reads whole: a skill folder, a file in an agents
   folder such as `~/.claude/agents/`, or a Codex rules file such as
   `~/.codex/rules/default.rules`.
   `unmanaged-link` is a link loadout did not create there; it has no content to port, so show
   the user where it points and ask before deleting it. Never read through it.
   `unparsable` is a file loadout shares with the harness that no longer parses: there is nothing
   to port from it, so tell the user and ask before replacing it with `loadout sync --force`, or,
   for one the source no longer renders, before deleting it.
   `source-changed`, `missing` and `orphaned` need only a sync.
2. Read the change. `loadout check` without `--json` prints each diff: `-` lines exist only on
   disk, `+` lines only in the source. It cuts each diff at 40 lines and says how many more there
   are; for the rest, read the file on disk and the source directly. For an `unmanaged` path, read
   the file or folder itself.
   A file holding a secret is never diffed: loadout prints its name, how many lines differ and
   why it counts as a secret. Compare the file on disk with the source directly to find the
   change, or better, move the secret into an environment variable (`auth_env_var = "<NAME>"`
   for a server's token, or `${NAME}` where the harness reads one), as “Secrets in the adopted
   source” in [onboarding](onboarding.md) describes, and never print the value back to the user.
3. Port each change into the source that owns it:
   - a global harness file goes to the global source, a project file to the project source, and a
     project `*.local*` file to the personal tier;
   - a new entry goes to the `default` profile, and an entry a profile defines goes back to that
     profile;
   - a structured entry (a permission, an MCP server, a hook, a setting) is written in its slice
     source's own form, not the harness's spelling: a Codex `prefix_rule` approval becomes a
     `[shell]` entry in `permissions.toml`;
   - for prose (instructions, a skill), decide whether the change applies to every harness or only
     to the one it was made in, and keep a harness-only change in a `::: <harness>` section.
4. Tell the user which harnesses the ported change now reaches. An approval clicked in one harness
   becomes a rule for every configured harness that supports it.
5. A `+` line missing from disk is an entry removed outside loadout. Ask whether to remove it from
   the source or restore it; do not decide.
6. Delete an `unmanaged` file or folder once its content is in the source.
7. Sync. When the harness copy still differs from the new render, sync refuses and writes
   nothing; show that diff, and run `sync --force` only after the user approves discarding it.
   Sync also cuts each diff at 40 lines, and when it cut one it writes the complete diff to
   `sync-abort.diff` in `$XDG_STATE_HOME/loadout/` (`~/.local/state/loadout/` when that is unset)
   and names the file: read it in full before asking for `--force`. For a file whose diff is
   withheld because it holds a secret, which neither the terminal nor that file shows, read the
   file on disk and compare it with the source first: `--force` replaces whatever it holds.

### Settings a harness saves itself

Harness commands save some settings into files loadout writes. That is drift: the source owns
those keys. Claude's and Droid's `settings.json`, OpenCode's `opencode.json`, Pi's `settings.json`
and pi-permission-system's `config.json` are written whole, so any key the harness adds or
changes there is `hand-edited`, set in the source or not. Codex's `config.toml` compares only the
keys the source sets, as `owned-key` or `owned-key-removed`.

| harness | action | key | file |
|---|---|---|---|
| Claude | `/model`; a model name retired by an upgrade | `model` | `~/.claude/settings.json` |
| Claude | `/effort`, or the effort in `/model` | `modelSettings` | `~/.claude/settings.json` |
| Claude | `/plugin` at user scope | `enabledPlugins`, `extraKnownMarketplaces` | `~/.claude/settings.json` |
| Claude | `/theme`, `/config` | `theme` and other keys | `~/.claude/settings.json` |
| Claude | making auto mode the default | `permissions.defaultMode` | `~/.claude/settings.json` |
| Codex | `/model` | `model`, `model_reasoning_effort` | `~/.codex/config.toml` |
| Codex | approving an MCP tool for good | `mcp_servers.<server>.tools.<tool>.approval_mode` | `~/.codex/config.toml` |
| Codex | the plugin browser | `plugins."<name>@<marketplace>"` | `~/.codex/config.toml` |
| Droid | `/model`, effort and autonomy pickers | `sessionDefaultSettings` | `~/.factory/settings.json` |
| Droid | `/settings`; plugin and marketplace installs | `theme` and other keys; `enabledPlugins`, `extraKnownMarketplaces` | `~/.factory/settings.json` |
| OpenCode | desktop app shell setting; `opencode plugin add`; `opencode mcp add --global` | `shell`, `plugins`, `mcp` | `~/.config/opencode/opencode.json` |
| Pi | `/model` or `/thinking`, then Ctrl+S | `defaultProvider`, `defaultModel`, `defaultThinkingLevel` | `~/.pi/agent/settings.json` |
| Pi | `/settings`, `/scoped-models`; `pi install`, `pi config` | `theme`, `modelThinkingLevels`, `enabledModels`; `packages` | `~/.pi/agent/settings.json` |
| Pi | `/permission-system` | `yoloMode`, `debugLog`, `permissionReviewLog` | `~/.pi/agent/extensions/pi-permission-system/config.json` |

Not drift: OpenCode's TUI model pick (`~/.local/state/opencode/model.json`) and TUI settings
(`~/.config/opencode/cli.json`), which loadout does not write; Droid's `trustedFolders` and Pi's
`lastChangelogVersion`, which the presets preserve; Pi's `settings.json` when `[pi]` does not
declare `plugins`; Codex keys the source does not set; Claude's project
`.claude/settings.local.json`, which loadout does not write.

Ask the user which answer they want:

1. Port the value into the source that owns the key and sync: the settings, `defaults` or
   `plugins` fragment, or `[mcp]` policy in `permissions.toml` for a Codex tool approval. Every
   machine gets it.
2. Hand the key to the harness: remove it from the fragment, if it is there, and add it to that
   agent block's `preserve`, such as `preserve = ["model", "modelSettings"]` under `[claude]`, then
   sync. Sync copies a preserved key from the file on disk, so a fresh machine starts without it.
   `preserve` takes top-level keys only (preserving `modelSettings` hands over every model's entry)
   and refuses a key the source or a slice writes: `permissions`, Claude's `hooks` and
   `enabledPlugins` and Droid's `enabledPlugins` when those slices are declared, and Pi's
   `packages`. Those can only be ported. pi-permission-system's keys have no place in the source,
   so preserving is the only answer for them. For Codex, removing the key from `defaults` is
   enough, and `[codex] preserve` is a configuration error; the next sync deletes it from
   `config.toml`, refusing while Codex's value differs from loadout's until the user approves
   `--force`.
