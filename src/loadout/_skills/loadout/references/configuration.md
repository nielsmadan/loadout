# Loadout configuration routing

Read scope selection, then choose the narrowest canonical slice source from the capability matrix.

- [Scope selection](#scope-selection)
- [Canonical producers](#canonical-producers)
- [Capability matrix](#capability-matrix)
- [Configured harnesses](#configured-harnesses)
- [Source rules](#source-rules)
- [Global profiles](#global-profiles)
- [Stealth sources](#stealth-sources)
- [Sync and failures](#sync-and-failures)
- [Porting a change made outside loadout](#porting-a-change-made-outside-loadout)

## Scope selection

| request scope | ownership | initialization | sync |
|---|---|---|---|
| personal, or none named | uncommitted configuration for this repository and user | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| project | committed configuration shared by this repository | `loadout/config.toml` must exist | `loadout sync --root <repo>` |
| global | this machine across repositories | machine config must exist | `loadout sync --global` |
| stealth-targeted repository: the user wants personal configuration in a repository that has no `loadout/config.toml` and that a stealth source targets (see Stealth sources below) | the stealth folder (or the `[[source]]` it names), never the repository | none: never run `init project` there, it exits 3 once the stealth source has written there | `loadout sync --stealth <name>` |

The request scope is what the user's request names, in words or as `--personal`, `--project` or
`--global` after the skill's name. It selects a row; it is not an argument to loadout. The sync
column gives the command to run.

Resolve a project from its root, the folder holding `loadout/config.toml`, not from an arbitrary
working subdirectory. That is the repository root unless the project was initialised in a
subfolder, such as `apps/web` in a monorepo; pass that folder as `--root`. For global scope, first resolve the actual
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
slice directories and harness blocks in `loadout.toml`. Do not invent destinations or edit generated
outputs.

Kits contribute through the same project slices. Project commands and support files
outside a known slice remain harness-owned and stay in place.

## Capability matrix

| artifact | personal project | shared project | global |
|---|---|---|---|
| `permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | `permissions.toml` from selected sources |
| `mcp-permissions` | `loadout/permissions.local.toml` | `loadout/permissions.toml` | MCP policy from selected `permissions.toml` sources |
| `mcp` | unsupported | `loadout/mcp.toml` | `mcp.toml` from selected sources |
| `instructions` | unsupported | `loadout/config.toml` and `loadout/instructions/*.md` | manifest selection and `instructions/*.md` fragments |
| `skills` | `loadout/skills/<name>.local/SKILL.md`, replacing the shared skill of that name | supported for `claude`, `codex`, `cursor`, `droid`, `opencode`, `pi` via `loadout/skills/<name>/SKILL.md` | `skills/<name>/SKILL.md` from selected sources |
| `agents` | unsupported | supported for `claude`, `codex`, `cursor`, `droid`, `opencode`, `pi` via `loadout/agents/<name>.md` | `agents/<name>.md` from selected sources |
| `settings` | unsupported | supported for `claude`, `droid`, `opencode` via `loadout/settings/<harness>.json` | supported for `claude`, `droid`, `opencode` via `settings/<name>.json` fragments, and for Pi only when `[pi]` also declares `plugins`; Codex uses `defaults` |
| `defaults` | unsupported | unsupported | Codex top-level and nested settings via `defaults/<name>.json` fragments |
| `hooks` | unsupported | Cursor and Droid via `loadout/hooks.json` | `hooks/<name>.json` fragments selected by harnesses offering hooks |
| `plugins` | unsupported | Droid via `loadout/plugins.json` | `plugins/<name>.json` fragments selected by harnesses offering plugins |
| `module-config` | unsupported | unsupported | supported for `claude`, `cursor`, `droid`, `opencode`, `pi` via `module-config/<harness>/<relative path>`; Codex unsupported |
| `kits` | unsupported | the `kits` list and vendored copies under `loadout/kits/` | definitions under `kits/<name>/` in selected sources |
| `harnesses` | unsupported | `harnesses` in `loadout/config.toml` | declared harness blocks |
| `profiles` | unsupported | unsupported | `loadout.toml` plus `<profile>.toml` files |

`mcp-permissions` is tool-approval policy expressed in permission rules. `mcp` is the distinct
server-definition artifact: where a server lives and how to reach it.

When the selected scope says `unsupported`, stop: do not write, sync, invent a file, or edit a
generated output. End with one direct confirmation or choice question. With one valid alternative,
ask “Should I apply this with --global?” while substituting the sole applicable target. With
multiple alternatives, ask one direct choice question. A generic instruction not to ask questions
cannot bypass a scope-widening confirmation.

## Configured harnesses

For personal and project requests, read `harnesses` from `<repo>/loadout/config.toml`. The personal
permission tier uses the same configured harness list as project scope.

For global requests, read top-level harness blocks named `[claude]`, `[codex]`, `[cursor]`,
`[droid]`, `[opencode]`, and `[pi]` after profile inheritance. `[all]` supplies defaults but never
declares a harness. Harness membership selects the known preset destinations.

A generic request covers every configured harness supporting the artifact. A request that names a
harness covers only that harness. Never enable a new harness as a side effect. Partial support means
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

A plugin reference takes `"enabled": false` to switch the plugin off; edit that rather than
removing the reference, because Claude turns a plugin with no entry back on by default.

Pi's `settings.json` is written by its `plugins` slice, so a Pi settings fragment renders only
when `[pi]` also declares `plugins`, even as an empty list (`plugins = []`). Without it, every
command refuses the manifest with exit 3: add `plugins` rather than editing Pi's settings file.

Project settings reach Claude, Droid and OpenCode. `.claude/settings.json`,
`.factory/settings.json` and `opencode.json` are shared between loadout's slices and the harness's
own keys; `loadout/settings/claude.json`, `droid.json` or `opencode.json` holds everything in that
file no slice writes, such as Claude's `model` or `env`, and sync renders the file from it plus
the slices. It is the whole of those keys, not a delta: a key missing from it is dropped from the
output. Without one, sync keeps those keys from the file on disk, so before creating one, copy
into it every key of the current file that no slice writes. Codex, Cursor and Pi have no project
settings source: Cursor reads only permissions from `.cursor/cli.json`. Init writes the settings source gitignored when the file it adopted was untracked or
ignored, so check whether Git ignores it before calling a change there shared.

Project MCP server definitions reach Claude, Cursor, Droid, OpenCode and Pi (`.pi/mcp.json`). Codex
project scope writes no server config; a stealth source writes Codex servers. Global MCP server definitions render
for all six configured harnesses. Claude's output owns only `mcpServers` in `.claude.json`; Droid
writes `.factory/mcp.json`.

Project skills reach all six harnesses. Codex reads `.agents/skills`, and so do Cursor, OpenCode
and Pi.

Cursor has no ask list. An `ask` entry is left to Cursor's `approvalMode`, and an `allow` that
would also cover an `ask` is not rendered for Cursor, so that command still prompts there. Tell
the user both when a rule they ask for reaches Cursor. Cursor has no global instructions file, so
global instructions do not reach it.

## Source rules

Permissions are TOML rules in four tables, and no others:

- `[shell]`: shell command rules for every harness;
- `[mcp]`: MCP tool rules, which supply the `mcp-permissions` artifact;
- `[claude.extra]`: Claude-only permission strings;
- `[opencode.extra]`: OpenCode-only permission keys.

Any other table or key is refused by name; fix the name it reports rather than moving the rules.
Preserve the existing order: OpenCode and Pi use last-match-wins
semantics after rendering. Project permissions merge kit, committed, then personal tiers;
use `permissions.local.toml` only for personal requests. Loadout never gitignores
`loadout/permissions.local.toml`: when you create it, ask whether to list
`/loadout/permissions.local.toml` in the project's `.gitignore` or in `.git/info/exclude`, which
keeps it off the shared list, and add it there before syncing.

MCP server definitions are tables in `mcp.toml`, separate from permission policy. Project scope
has one committed `loadout/mcp.toml`; global scope composes selected sources last-wins. A server
request and a tool-approval request therefore change different source files even when they name
the same server.

Global module configuration is copied byte-for-byte from
`module-config/<harness>/<relative path>` to that harness's configuration directory. Claude, Cursor,
Droid, OpenCode and Pi offer this slice; Codex does not. The relative path is authored by the module and must not
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
`[skills.<name>] agents = [...]` narrows the default set of configured consumers. Kits are
listed by name; use `loadout kit add <name> --linked`, `--vendored`, or
`kit sync <name>` when that command exactly expresses the request.

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

After adding a kit, and whenever you edit a project's instructions while it lists one,
compare the kit's instruction parts (`loadout kit list` shows where each kit
resolves) with the project's fragments. Look for a section with the same heading text and for
bullets that restate the same rule, even in other words. Propose deleting those duplicates from
the project fragment, never from the kit, and keep every rule specific to the project: its
names, paths, versions and exceptions. Show the proposed deletions and apply them only once the
user approves. A `project.instructions` note that the document repeats a heading points at one
such pair. When `kit add` reports that a project skill is byte for byte the one the kit
supplies, offer to delete `loadout/skills/<name>/` so the project follows the kit's copy.

The same note at global scope, `<harness>.instructions`, names two fragments of one harness's
instruction document that bring the same heading. Compare them the same way and propose keeping
one section. Before deleting the repeat from a fragment, find every harness block that names it, so
no other harness loses the rule, and apply the deletion only once the user approves.

Agents (subagents) are `agents/<name>.md` in a global source, or `loadout/agents/<name>.md` in a
project, and reach every configured harness: Claude, Codex (as TOML), Cursor, Droid (as a custom
droid), OpenCode and Pi (which needs the `pi-subagents` package). The frontmatter takes `description`
(required), an optional `name` equal to the file name, `model` and `effort`; the body is the
agent's instructions, with `::: <harness>` sections. `model` is `inherit` or a per-harness map, one
indented `<harness>: <model>` line each; a harness left out inherits its parent's model, and
OpenCode's needs the `provider/model-id` form. A single model name for every harness is an error,
because no name means the same model everywhere. `effort` is one of `off`, `minimal`, `low`,
`medium`, `high`, `xhigh`, `max`, translated per harness (a missing level becomes the nearest
lower one, with a note; Cursor and OpenCode have no effort, and Cursor takes it in the model,
as `cursor: <model>[effort=high]`), or a per-harness map written verbatim. Anything
harness-specific, such as Claude's `tools`, goes in a block keyed by that harness; Droid's `droid:`
block takes only `tools`, `mcpServers` and `version`, and any other key is refused because Droid
drops it. A request about
one harness's agent changes that harness's block or map entry, not the shared body.

Global fragments resolve through `[[source]]` entries. A bare fragment name must resolve uniquely;
use `source/name` when two sources offer it. Reuse an existing selected fragment when ownership is
already narrow enough. Otherwise create a clearly named fragment under the selected source and add
that name to the appropriate harness block. `[all]` is appropriate only when the representation and
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

Before changing a fragment, find every manifest or project-config entry that consumes it. A
harness-specific request must not alter a shared fragment for other harnesses; split the source
or ask which effect the user wants.

## Global profiles

`loadout.toml` is the `default` profile. A non-default `<profile>.toml` declares
`extends = "default"` and overrides only its deltas. The machine config may select the active
profile. Parent names identify sibling files, not paths; profile symlinks must stay within the
source directory. Internal aliases use canonical identities for cycles and dependency protection.

Harness blocks and each legacy instruction/permission target inherit per field. A supplied list or
map replaces the entire field. Top-level `remove = ["instructions.claude.output"]` deletes an
inherited TOML key path before child fields apply; remove a whole target before redeclaring it
for wholesale replacement. Missing/overlapping paths and traversal through lists are errors.
Removing a harness field exposes applicable `[all]` defaults. Legacy instruction targets also
accept `substitute`. Reuse shared fragments across profiles and vary only the final fragment, as
in `settings = ["pi", "pi-autonomous"]`. Project configs and kit catalogs have no
`extends`; catalogs compose selected parts.

When a non-default profile is active and the request names no profile, ask whether the change is:

- active-profile only, written as a delta in `<profile>.toml` and its selected fragments; or
- inherited default, written to `loadout.toml` and its default fragments.

Write only when the user explicitly selects active-profile-only or inherited-default in the
original request or a reply. No question is needed when the active profile is `default` or the
request names its intended profile.

## Stealth sources

A stealth source is personal, uncommitted configuration for repositories that have not adopted loadout,
which the user cannot add a `loadout/` folder to. It is a registered folder outside those
repositories whose `loadout.toml` has a `[stealth]` table with `targets` patterns. Use it when
the user wants their own instructions, MCP servers, permissions or skills in such a repository.
Use a personal project source instead when the repository has `loadout/config.toml`, and the
global source when the change belongs to the whole machine. loadout 0.10.0 called it an overlay;
`loadout overlay`, `--overlay` and an `[overlay]` table are refused. Before renaming anything,
remove each 0.10.0 overlay with
`uvx --python 3.13 --from git+https://github.com/nielsmadan/loadout.git@v0.10.0 loadout overlay remove NAME --yes`.
Until then `stealth init`, `stealth add`, `stealth remove` and stealth `sync` and `check` refuse with exit 3 while
0.10.0's `overlays.toml` lists an overlay, and so do `sync`, `check` and `init project` in a target
whose `.git/info/exclude` holds an overlay block; each message names what it found.
`stealth list` does not refuse: it exits 0 and, after the list, warns on stderr with the same
message (or says `overlays.toml` cannot be read). Then use `loadout stealth`, `--stealth` and
`[stealth]`.

1. Find it: `loadout stealth list` prints each stealth source's name, folder and `targets`. A repository
   is covered when a pattern matches its top folder. If none does, ask before creating or
   registering one, since a registered folder writes into every repository its patterns match.
   To start one, run `loadout stealth init <folder> --targets "<pattern>" --yes` once the user
   agrees: it writes a minimal `loadout.toml` and registers the folder, and refuses a folder that
   already has a `loadout.toml`. Then edit the folder as in step 2. For a folder that already has
   a `loadout.toml` with a `[stealth]` table, run `loadout stealth add <folder>` instead.
2. Edit the stealth folder, never the files it renders. Its source layout is a global source's:
   `instructions/<name>.md`, `permissions.toml`, `mcp.toml`, `skills/` and `settings/<name>.json`
   (Claude and Droid), named in its `loadout.toml`. The rendered files are
   `CLAUDE.local.md`, `.claude/settings.local.json`, `.claude/skills/`, `.codex/config.toml`,
   `.codex/rules/<name>.rules`, `.agents/skills/`, `.cursor/rules/<name>.mdc`, `.cursor/cli.json`,
   `.cursor/mcp.json`, `.cursor/skills/`, `.factory/AGENTS.md`, `.factory/mcp.json`,
   `.factory/settings.local.json`, `.factory/skills/`, `opencode.json`, `.opencode/skills/`,
   `.pi/` and the servers under `projects.<repository>.mcpServers` in `~/.claude.json`. An edit
   made there is refused or overwritten.
3. A stealth source renders only instructions, `mcp` server definitions, permissions (with `settings`
   for Claude and Droid) and skills. Hooks, plugins, agents, module config and Codex defaults are
   refused with a reason; report that rather than looking for another route. `mcp = [...]` with
   names is refused, since the stealth source's `[[source]]` entries supply its servers. `preserve` is refused too: a
   stealth source owns only the keys it writes. `follow_worktrees` and `git_hook` are reserved and
   refused when `true`; never set them.
   To reuse a kit from the global source, link it rather than copying its parts into the stealth
   folder: `loadout kit add <kit> --stealth <name>` adds it to `kits` in the `[stealth]` table, and
   `loadout kit list --stealth <name>` shows where it resolves. A stealth source only links kits;
   never pass `--vendored` with `--stealth`. The stealth folder's own instructions come first, and
   its own rules, servers and skills outrank the kit's, so a same-named skill or server in the
   folder hides the kit's. When `kit add` prints a note that a skill or instruction fragment in the
   folder is byte for byte the kit's, offer to delete that copy (and drop the fragment from the
   `instructions` lists) as the note says, so the stealth source follows the kit. A kit that
   brings hooks or plugins is refused; report it.
4. Preview and apply with `loadout check --stealth <name>` and `loadout sync --stealth <name>`, or
   plain `loadout sync` from inside a target repository. `loadout sync --global` also syncs every
   stealth source, but `--force` then reaches only the global source.
5. A key in a stealth source's settings fragment that the harness rewrites (Claude's `/model`, say) is
   an owned key edited outside loadout. Port the harness's value into the stealth source's
   `settings/<name>.json` and sync. `preserve` is refused in a stealth source, so the key cannot be
   handed to the harness: removing it from the fragment makes the next sync delete it from the
   file, the harness's value with it. Do that only when the user asks, and say so first.
6. A repository that has adopted loadout (it has `loadout/config.toml`) is not a stealth target.
   Its stealth sources retire their files there on the next sync, before the project syncs. When `check`
   there prints `adopted loadout; the stealth source retired its files here`, or `skipped: adopted
   loadout`, run `loadout sync` before porting anything: the stealth source's files in it are the user's
   personal configuration, never the project's, and must not be ported into `loadout/`.
7. Stealth refusals have their own meaning. A path the team tracks, a path another owner records,
   a key or server the user defined and a path git would not ignore (exit 3) cannot be forced:
   report them. An output or key edited outside loadout (exit 1) follows “Porting a change made
   outside loadout”, with `--stealth <name>` in place of `--root`. A note `saved by the harness, not in
   the stealth source: <file>: <list>: <rules>` lists approvals Claude or Droid saved: ask whether to add them to the stealth source's
   `permissions.toml` or delete them; it never blocks sync. When the note lists `a rule not shown because it
   holds a secret`, that saved rule holds a credential; tell the user to read the file, and never copy the rule into `permissions.toml`.
8. `loadout stealth remove <name>` retires everything the stealth source wrote. Run it only when the user
   asks. Never edit `stealth.toml`, a repository's `.git/info/exclude` or `~/.claude.json` by
   hand to get past a refusal.

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

Finish by naming changed source files, configured harnesses reached, unsupported or excluded
harnesses, and whether sync completed.

## Porting a change made outside loadout

The source is the only place configuration lives. A change made in a harness's own UI or CLI, such
as a Codex approval, `claude mcp add` or an installer adding a hook, is ported into the source and
the harness copy then replaced or deleted. Never leave it in the harness beside the source. Loadout
flags these changes; it does not merge them.

1. Run `loadout check --json` with the scope's `--root <repo>`, `--global` or `--stealth <name>`, and read each
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
     project `*.local*` file to the personal tier. For a stealth output or key (the scope is
     `--stealth <name>`), none of these applies: the owning source is the stealth folder, or the
     `[[source]]` its `loadout.toml` names, even though the file sits in the target repository.
     Never write `loadout/` into the target repository and never run `init project` there;
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
| Pi | `/mcp`: enable, disable, exposure; "Disable in this project" | `enabled`, `exposure`, `toolExposure` in a server's entry; an override entry | `~/.pi/agent/mcp.json`, `.pi/mcp.json` |

Not drift: OpenCode's TUI model pick (`~/.local/state/opencode/model.json`) and TUI settings
(`~/.config/opencode/cli.json`), which loadout does not write; Droid's `trustedFolders` and Pi's
`lastChangelogVersion`, which the presets preserve; every key of Cursor's `cli-config.json`
except `permissions`, which the CLI rewrites with its own defaults and loadout does not own; Pi's `settings.json` when `[pi]` does not
declare `plugins`; Codex keys the source does not set; Claude's project
`.claude/settings.local.json`, which a project source does not write.

Ask the user which answer they want:

1. Port the value into the source that owns the key and sync: the settings, `defaults` or
   `plugins` fragment, or `[mcp]` policy in `permissions.toml` for a Codex tool approval. Every
   machine gets it.
2. Hand the key to the harness (not for a stealth source, which refuses `preserve`; see Stealth sources):
   remove it from the fragment, if it is there, and add it to that harness block's `preserve`, such as `preserve = ["model", "modelSettings"]` under `[claude]`, then
   sync. Sync copies a preserved key from the file on disk, so a fresh machine starts without it.
   `preserve` takes top-level keys only (preserving `modelSettings` hands over every model's entry)
   and refuses a key the source or a slice writes: `permissions`, Claude's `hooks` and
   `enabledPlugins` and Droid's `enabledPlugins` when those slices are declared, and Pi's
   `packages`. Those can only be ported. Pi's `/mcp` changes sit inside `mcpServers`, beyond
`preserve`, and the source has no form for exposure; a server turned off ports as an `[mcp]` deny
rule (`"docs/*"`). pi-permission-system's keys have no place in the source,
   so preserving is the only answer for them. For Codex, removing the key from `defaults` is
   enough, and `[codex] preserve` is a configuration error; the next sync deletes it from
   `config.toml`, refusing while Codex's value differs from loadout's until the user approves
   `--force`.
