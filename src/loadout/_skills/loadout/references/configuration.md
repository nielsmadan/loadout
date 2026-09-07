# Loadout configuration routing

Read scope selection, then native producers when an `artifacts` reference exists. The capability
matrix and legacy source rules apply only to routes produced by the legacy presets.

- [Scope selection](#scope-selection)
- [Native producers](#native-producers)
- [Capability matrix](#capability-matrix)
- [Configured agents](#configured-agents)
- [Source rules](#source-rules)
- [Global profiles](#global-profiles)
- [Sync and failures](#sync-and-failures)

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
directory and optionally the active profile.

If the scope is absent, use the onboarding reference when setup is requested. An ordinary settings
change does not itself authorize migration and its Git checkpoint; report the missing setup.

## Native producers

Read the config's `artifacts` file, resolved relative to that config. For each requested category,
find records whose `agents` include the configured consumer, then follow `parts.<category>.source`
or the opaque record's `category` and `source`. Sources are relative to the owning config's
directory, even when its artifacts index is nested. Project `presets = false` means these routes
are the complete producer set: legacy
filenames and unsupported entries in the matrix below do not describe these native capabilities.

Native JSON/TOML contributors preserve literal null, false, arrays and ordered objects. A part's
explicit `keys` reserve its fields even when empty; without `keys`, its present top-level keys are
owned. A `renderer` part uses the named portable permission format instead. Each field has one
producer. Never add a portable overlay that hides native edits or a contributor claiming another
part's keys. A tree owns descendants: add a skill within its existing source tree, never a
colliding child artifact route. Optional private sources under category `local/` remain private.

For copied files, inspect `mode` or tree `modes` in the artifact record. Migration authors those
full filesystem modes because Git retains only executable bits. Change a declared mode there;
source `chmod` only controls outputs without an override. New tree files use source modes until
given a `modes` entry. Move the corresponding relative mode key when renaming a file whose
override should follow it.

For personal requests, use an existing declared personal producer only if it can represent the
change. If none exists, ask before editing committed/shared/global source or changing ownership.
Do not assume a legacy `permissions.local.toml` participates in a native project. Preserve private
mode and ignore rules. Ask before a consumer-specific change affects other agents sharing a part.

Global native Pi settings can own `defaultModel`; native Claude MCP can own `mcpServers` inside
the mixed runtime registration. Follow those explicit producers instead of applying the legacy
limitations below. Partial ownership preserves runtime/auth fields; never promote the whole live
document into committed source. Categories without routes need an explicit binding decision.

Native project instruction routes with `template_instructions = true` prepend the selected
templates' `instructions.md` before the route's own source body. Edit that body for project rules;
edit `loadout/templates/<name>/instructions.md` only when changing the template tier. Template
copies keep hash provenance, and `template sync` refuses modified copies. Other populated template
categories cannot overlay native producers: edit their declared artifact sources instead.

## Capability matrix

Legacy preset routes only; check declared native producers first.

| artifact | personal project | shared project | global |
|---|---|---|---|
| `permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | `permissions.toml` from selected sources |
| `mcp-permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | MCP policy from selected `permissions.toml` sources |
| `mcp` | unsupported | `loadout/mcp.toml` | `mcp.toml` from selected sources |
| `instructions` | unsupported | `loadout/config.toml` and `loadout/instructions/*.md` | manifest selection and `instructions/*.md` fragments |
| `skills` | unsupported | supported for `claude`, `opencode`, `pi` via `loadout/skills/<name>/`; Codex unsupported | `skills/<name>/` trees from selected sources |
| `settings` | unsupported | unsupported | supported for `claude`, `opencode` via `settings/<name>.json` fragments; Codex uses `defaults`; Pi unsupported |
| `defaults` | unsupported | unsupported | Codex top-level and nested settings via `defaults/<name>.json` fragments |
| `hooks` | unsupported | unsupported | `hooks/<name>.json` fragments selected by agents offering hooks |
| `plugins` | unsupported | unsupported | `plugins/<name>.json` fragments selected by agents offering plugins |
| `module-config` | unsupported | unsupported | supported for `claude`, `opencode`, `pi` via `module-config/<agent>/<relative path>`; Codex unsupported |
| `templates` | unsupported | declarations and vendored copies under `loadout/templates/` | definitions under `templates/<name>/` in declared sources |
| `harnesses` | unsupported | `harnesses` in `loadout/config.toml` | declared agent blocks or legacy targets |
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

For global requests, prefer top-level agent blocks named `[claude]`, `[codex]`, `[opencode]`, and
`[pi]` in the selected profile after inheritance. `[all]` supplies defaults but never declares an
agent. During the legacy transition, also recognize explicit `[instructions.<name>]` and
`[permissions.<name>]` targets by their renderer and destination. If a legacy target's arbitrary
name, renderer, and destination do not establish one harness unambiguously, ask rather than
guessing or enabling an agent.
Also include artifact records' `agents`, filtered by the requested category. Agent membership does
not itself create an output route. `harness add` refuses native projects until their producer
routes are explicitly designed; do not add a harness name alone and claim it receives configuration.

A generic request covers every configured agent supporting the artifact. A request that names an
agent covers only that agent. Never enable a new harness as a side effect. Partial support means
apply the supported mappings and report each exclusion; it is a question only when two valid
mappings have materially different effects.

For legacy presets, global settings fragments reach Claude and OpenCode because their document renderers preserve the
settings residual. Codex settings use the separate `defaults` slice and
`defaults/<name>.json` fragments, including nested settings. For the available-skills catalog
budget, use `{"skills": {"max_context_tokens": 10000}}`, not a dotted JSON key. Loadout owns
only the named leaf, preserving other `skills` fields and `[[skills.config]]` overrides.
Empty objects own nothing; arrays are managed as whole fields. If surgery refuses an inline
parent table or a child inside an array-table element, report the conflict rather than widening
ownership. `$remove` entries use TOML key paths, such as `"skills.max_context_tokens"`.
Pi's permission document does not preserve a settings residual, so report Pi as unsupported instead
of editing its harness-owned settings file.

For legacy presets, project MCP server definitions reach Claude and OpenCode directly. Pi reads Claude's `.mcp.json`
when that shared destination is present; a Pi-only project has no MCP output. Codex has no verified
project MCP destination. Global MCP server definitions render for all four configured agents;
Claude's output is staged for a separate `claude mcp add-json` step rather than written into its
runtime state, so report that remaining application step.

For legacy presets, project skills reach Claude, OpenCode, and Pi. Codex has no verified project skills directory, so
report it as unsupported for a Codex-only or Codex-specific project skill request.

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
`module-config/<agent>/<relative path>` to that agent's configuration directory. Claude, OpenCode
and Pi offer this slice; Codex does not. The relative path is authored by the module and must not
be derived from its package name. An OpenCode plugin's `.ts` file belongs here rather than under
`plugins`, which renders enablement OpenCode has no list for.

Project instructions are named in `loadout/config.toml` and stored in
`loadout/instructions/<name>.md`. Project skills are whole trees under
`loadout/skills/<name>/`. Templates are declared by name; use `loadout template add`, `vendor`, or
`sync` when that command exactly expresses the request.

Global fragments resolve through `[[source]]` entries. A bare fragment name must resolve uniquely;
use `source/name` when two sources offer it. Reuse an existing selected fragment when ownership is
already narrow enough. Otherwise create a clearly named fragment under the selected source and add
that name to the appropriate agent block. `[all]` is appropriate only when the representation and
value are genuinely shared by every declared consumer.

Before changing a fragment, find every manifest or project-config entry that consumes it. An
agent-specific request must not alter a shared fragment for other agents; split the source or ask
which effect the user wants.

## Global profiles

`loadout.toml` is the `default` profile. A non-default `<profile>.toml` declares
`extends = "default"` and overrides only its deltas. The machine config may select the active
profile.

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
refuses an output modified outside loadout, leave the requested source edit visible and report the
conflicting path. Use `--force` only after an explicit request to discard those external edits.

For a permission error, verify the exact blocked path using the running environment's sandbox
diagnostics, report that path and verdict, and stop. Do not relocate the source or weaken the
operation to work around a real denial.

Finish by naming changed source files, configured agents reached, unsupported or excluded agents,
and whether sync completed.
