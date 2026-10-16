# Loadout configuration routing

Read scope selection, then choose the narrowest canonical slice source from the capability matrix.

- [Scope selection](#scope-selection)
- [Capability matrix](#capability-matrix)
- [Configured agents](#configured-agents)
- [Source rules](#source-rules)
- [Global profiles](#global-profiles)
- [Sync and failures](#sync-and-failures)
- [Porting a change made outside loadout](#porting-a-change-made-outside-loadout)

## Scope selection

| invocation | ownership | initialization | sync |
|---|---|---|---|
| omitted or `--personal` | uncommitted configuration for this repository and user | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| `--project` | committed configuration shared by this repository | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| `--global` | this machine across repositories | machine config must exist | `loadout sync --global` |

Resolve a project from the repository root, not from an arbitrary working subdirectory. The
project config is `<repo>/loadout/config.toml`. For global scope, first resolve the actual
`XDG_CONFIG_HOME` environment value: when it is non-empty, inspect only
`$XDG_CONFIG_HOME/loadout/config.toml`; when it is unset or empty, inspect only
`~/.config/loadout/config.toml`. Never probe both. The machine config names the global source
directory, optionally the active profile, and optionally the default harnesses for fresh project
init. Explicit `--harness` selections override that default; initialized projects keep their own
configured harnesses.

If the scope is absent, use the onboarding reference when setup is requested. An ordinary settings
change does not itself authorize a migration; report the missing setup.

## Canonical producers

Project configuration uses fixed sources under `loadout/`: `permissions.toml`, optional
`permissions.local.toml`, named `instructions/` fragments, `skills/`, `mcp.toml`, `hooks.json`
and `plugins.json`. Global sources use the corresponding slice directories and agent blocks in
`loadout.toml`. Do not invent destinations or edit generated outputs.

Templates contribute through the same project slices. Project commands and support files
outside a known slice remain harness-owned and stay in place.

## Capability matrix

| artifact | personal project | shared project | global |
|---|---|---|---|
| `permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | `permissions.toml` from selected sources |
| `mcp-permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | MCP policy from selected `permissions.toml` sources |
| `mcp` | unsupported | `loadout/mcp.toml` | `mcp.toml` from selected sources |
| `instructions` | unsupported | `loadout/config.toml` and `loadout/instructions/*.md` | manifest selection and `instructions/*.md` fragments |
| `skills` | unsupported | supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/skills/<name>/SKILL.md` | `skills/<name>/SKILL.md` from selected sources |
| `agents` | unsupported | supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/agents/<name>.md` | `agents/<name>.md` from selected sources |
| `settings` | unsupported | unsupported | supported for `claude`, `droid`, `opencode` via `settings/<name>.json` fragments; Codex uses `defaults`; Pi unsupported |
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
an agent. Agent membership selects the known preset destinations; no route authoring is needed.

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
Pi's permission document does not preserve a settings residual, so report Pi as unsupported instead
of editing its harness-owned settings file.

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
use `permissions.local.toml` only for personal requests.

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
`<name>.local/SKILL.md` replaces the complete public skill privately.
`[skills.<name>] agents = [...]` narrows the default set of configured consumers. Templates are
declared by name; use `loadout template add <name> --tracked`, `--vendored`, or
`template sync <name>` when that command exactly expresses the request.

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
   would write. `unmanaged` is a file or folder loadout did not write, inside a folder its harness
   reads whole: a skill folder, or a Codex rules file such as `~/.codex/rules/default.rules`.
   `source-changed`, `missing` and `orphaned` need only a sync.
2. Read the change. `loadout check` without `--json` prints each diff: `-` lines exist only on
   disk, `+` lines only in the source. For an `unmanaged` path, read the file or folder itself.
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
7. Sync. When the harness copy still differs from the new render, sync refuses; show that diff,
   and run `sync --force` only after the user approves discarding it.
