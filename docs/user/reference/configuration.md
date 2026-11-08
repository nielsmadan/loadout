---
title: Configuration files
description: The machine configuration, the global manifest, project configuration, stealth sources, the permissions format, and the source layouts.
---

# Configuration files

Loadout uses TOML for source selection and permission rules, Markdown for instructions, skills and
subagents, and JSON for native configuration fragments. This page is the schema. What `sync` does
with the files it writes is described in [project setup](../guides/projects.md) and in the
[sync reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md).

## Machine configuration

```toml title="~/.config/loadout/config.toml"
source = "~/agent-config/loadout"
profile = "default"
harnesses = ["claude", "codex"]
```

When `XDG_CONFIG_HOME` is set, the location is `$XDG_CONFIG_HOME/loadout/config.toml`.

| Key | Meaning |
| --- | --- |
| `source` | Required. The directory holding the global `loadout.toml`; absolute or `~/`, and it must exist. A relative path is an error, since it would register whatever folder a command runs in |
| `profile` | Optional. The active profile at the registered source; without it, `default` |
| `harnesses` | Optional. The harnesses a fresh `init project` and `stealth init` select when no `--harness` is given. Non-empty, no duplicates, supported names only |

Any other key is an error, so a typo fails loudly. `loadout init global` writes this file;
the folder it names is the one source that may render global scope. A `--harness` given to
`init project` overrides the `harnesses` default, and an existing project keeps the harnesses in its
own config.

A missing machine config is not an error: the machine has no global scope, which is correct for
someone who only uses project scope.

The file is machine state: never version-controlled and never generated. It is not the only thing
loadout keeps on the machine. `sync` records what it wrote, per root, in
`$XDG_CONFIG_HOME/loadout/written/` (a stealth source's own record is in `stealth-written/`, and the
stealth sources themselves are registered in [`stealth.toml`](#stealthtoml)), and init's backups and a refused sync's full diff,
`sync-abort.diff`, and the lock files sync takes, in `stealth-locks/`, live under
`$XDG_STATE_HOME/loadout/` (`~/.local/state/loadout/` when unset).
None of it is stamped into a generated file
([0008](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0008-generated-files-carry-no-machine-state.md);
see also [0010](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0010-a-machine-config-locates-the-global-source.md)
and [0021](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0021-global-init-records-project-harness-defaults.md)).
A destination template also reads environment variables at render time; see
[destination templates](#destination-templates).

## Global manifest

A global source contains `loadout.toml`:

```toml
[[source]]
name = "personal"
path = "."

[all]
instructions = ["workflow"]

[claude]
settings = "claude"

[codex]
defaults = "codex"

[opencode]
[pi]
```

This example also requires `instructions/workflow.md`, `settings/claude.json`, and
`defaults/codex.json` in the source. Remove a named selection if you do not need it.

A `loadout.toml` renders only from this machine's registered source, the directory the machine
config names. Anywhere else, a project included, a root `loadout.toml` is refused with exit 3
before anything is read or written, so a cloned repository or a pull request cannot write under
your home directory
([0031](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0031-global-scope-renders-only-from-the-registered-source.md)).
A top-level key the manifest does not read, whether a table or a value, is refused by name.

A manifest needs at least one `[[source]]` and at least one target: a harness block such as
`[codex]`, an `[instructions.<name>]` target or a `[permissions.<name>]` target. With none, it is
refused with `no [<harness>], [instructions.<harness>] or [permissions.<name>] targets declared`.

### Source entries

| Field | Meaning |
| --- | --- |
| `name` | Name used to qualify fragments and kits, as `name/fragment` |
| `path` | Source directory: absolute, `~/`, or relative to the manifest. It must exist |
| `use` | Optional list of the categories this source contributes; without it, every category |
| `overrides` | Optional table of skills, agents or module files this source replaces in an earlier one |

The categories `use` accepts are `instructions`, `settings`, `permissions`, `hooks`, `mcp`,
`plugins`, `defaults`, `skills`, `agents`, `module-config` and `kits`.

Loadout does not fetch, version or distribute a source. Getting a shared repository onto your disk
is git's job.

### Several sources

`[[source]]` is an ordered list:

```toml
[[source]]
name = "company"          # a repo you cloned; loadout never fetches it
path = "~/src/acme-loadout"

[[source]]
name = "me"
path = "."

[source.overrides]
skills = ["review"]
module-config = ["pi/extensions/status/config.json"]
```

- **Permissions** merge every contributing `permissions.toml`. A source without one contributes no
  rules, and when no source has one the permission outputs render as they would from an empty file.
  Merging is union with **deny wins**: a deny in any source beats an allow in any other, in either
  order. Order still matters for emission, since OpenCode and Pi resolve last-match-wins, so
  entries from earlier sources are emitted first.
- **`[shell] default`** resolves the same way: the strictest value any source states wins. A source
  that omits the key casts no vote, so it never tightens one that set it
  ([0016](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0016-a-catch-all-is-stated-only-where-the-document-carries-it.md)).
- **MCP server definitions** use the last source for each server name.
- **Named fragments** need a name only one source declares, or a `source/name` qualifier. An
  unqualified name that more than one source declares is refused, naming the candidates.
- **Skills, subagents and module files** need an explicit override to replace an earlier source's.
  Each override selects the replacing source's complete skill tree, agent file or module file. It
  must name an item this source offers and an earlier source also offers; an undeclared collision
  is an error. Module paths include the harness name. An override category must be in that
  source's `use`.

See [composition](../guides/composition.md) and the
[composition rules](https://github.com/nielsmadan/loadout/blob/main/docs/reference/composition.md).

### Harness blocks

A harness block names a harness and the slices it takes. Destinations come from a built-in preset,
so a manifest never spells out a machine path.

| Key | Value |
| --- | --- |
| `instructions` | Ordered list of Markdown fragment names |
| `settings` | JSON settings fragment name or ordered list; an error under `[codex]`, whose settings are `defaults` |
| `defaults` | Codex defaults fragment name or ordered list |
| `hooks` | Hook fragment name or ordered list |
| `plugins` | Plugin fragment name or ordered list, for supporting harnesses |
| `substitute` | Map from selected instruction name to replacement name |
| `preserve` | Foreign top-level keys to carry forward where the target supports them; an error under `[codex]`, where loadout already leaves the keys it does not own alone |
| `permissions`, `mcp-permissions`, `mcp`, `skills`, `agents`, `module-config` | Automatic; `true` is the same as leaving the key out, `false` opts out, and `permissions = []` selects no rules |

`[codex]` and `[pi]` with no keys are complete declarations. An unknown harness name, or a slice a
harness does not offer, is an error listing what is available.

**Automatic slices.** `permissions`, `mcp-permissions`, `mcp`, `skills`, `agents` and
`module-config` render without being asked for, for harnesses that offer them, because none has an
authoring decision to make. For the last three the directory is the declaration, so dropping a
tree in renders it. Any of them takes `= false` to opt out; an absent key, or `= true`, means automatic:

```toml
[codex]
skills = false
mcp = false
```

`permissions = []` keeps the permissions target but selects none of the source's rules, so its
lists render empty while the settings around them still render: an autonomous profile's
`settings.json` with no `deny` for the classifier to defer to. `permissions = false` instead stops
managing the file, and the settings riding on it go too.

**Named slices.** `instructions` must be named, since it needs an order and alphabetical is wrong.
`settings` must be named because it is an input rather than an output. `hooks` and `plugins` must
be named because which hooks run and which plugins are on are authoring decisions, so an absent key
means loadout does not manage them, not none. `defaults` must be named for the reason given under
[Codex defaults](#codex-defaults).

`[all]` supplies defaults to the harnesses you declared, so shared configuration is written once:

```toml
[all]
instructions = ["intro-shared", "web-fetching.shared"]

[codex]
[pi]
```

It does not declare harnesses: a harness still has to be named, or adding `[all]` would enable
every harness. Each harness's own key replaces the `[all]` default. A default a harness has no
slice for is ignored; a key a harness block names itself is still checked.

`substitute` swaps one fragment for another without restating a list, and works in
`[instructions.<name>]` targets too:

```toml
[claude]
substitute = { git-policy = "git-policy.autonomous" }
```

Nothing is inferred from a filename: `git-policy.autonomous.md` is just a name, and the swap is
declared where it applies. When two fragments of one instruction document bring the same heading,
`check` and `sync` print a note naming the heading and both fragments. It never fails either
command.

### Hooks and plugins

`hooks` and `plugins` take fragment names, resolved as `<source>/hooks/<name>.json` and
`<source>/plugins/<name>.json` and deep-merged in order: maps merge key by key, lists concatenate,
and `null` removes. A plugin reference takes `enabled = false` to switch it off, so a profile
switches one off with a one-key fragment overlay instead of restating a list:
`{"plugins": {"my-tools": {"enabled": false}}}`. Claude and Droid render it as `false`; Codex and
Pi leave the plugin out. See [plugins](https://github.com/nielsmadan/loadout/blob/main/docs/reference/plugins.md)
for what a plugin reference holds and what each harness makes of it.

Pi's plugin declaration shares `settings.json` with `lastChangelogVersion`, a cursor Pi updates
after upgrades. The built-in Pi preset preserves that live key; it does not belong in a settings
fragment, and loadout refuses a fragment that tries to manage it. Pi's `settings` fragments are
written only through that document, so they render only when `[pi]` declares `plugins`, even as
`plugins = []`. `settings` under `[pi]` without `plugins` is a configuration error, exit 3, in
every command that reads the manifest. A `settings` list in `[all]` does not count: it reaches Pi
only when `[pi]` declares `plugins`.

### Module configuration

`module-config` carries a module's own content: Pi's `pi-statusline.json`, a Claude hook script
the `hooks` slice names by path, an OpenCode plugin's `.ts` file. Files under
`<source>/module-config/<harness>/` are copied verbatim to the same relative path beneath that
harness's config directory, keeping the module's formatting and any executable bit. The path is
authored rather than derived because it has to be: `pi-subagents` reads
`extensions/subagent/config.json`. Module configuration renders for Claude, Droid, OpenCode and
Pi. See [module config](https://github.com/nielsmadan/loadout/blob/main/docs/reference/module-config.md).

### Codex defaults

`defaults` (Codex only) manages selected keys of `~/.codex/config.toml`, a file loadout does not
own, and it strips every key it manages. A machine that never asked for it must never have its
hand-maintained Codex settings touched, so absence means loadout manages none of them. It takes
fragment names resolved as `<source>/defaults/<name>.json`, whose objects become TOML tables:

```toml
[codex]
defaults = "codex"        # defaults/codex.json: model, model_reasoning_effort, …
```

Nested objects own their leaf fields, not the entire parent table. This fragment sets the
available-skills catalog budget without managing other `skills` settings:

```json
{
  "skills": {
    "max_context_tokens": 10000
  }
}
```

It renders `max_context_tokens = 10000` under `[skills]` and preserves `[[skills.config]]`
overrides. An empty object owns nothing. Arrays are owned as a whole field, not element by element.
Use nested JSON objects, not a literal `"skills.max_context_tokens"` JSON key: that is a distinct,
quoted TOML key. See
[nested Codex defaults](https://github.com/nielsmadan/loadout/blob/main/docs/reference/codex.md#nested-defaults).

Because the key names are yours rather than a set loadout could enumerate, the slice keeps an
owned-key record beside its fragment, `defaults/<name>.owned`, generated and committed. It is what
lets removing a key from the fragment remove it from `config.toml`, instead of stranding it there
with nothing able to say it was ever managed. Edit the fragment, never the record; `loadout check`
reports a record that disagrees with it
([0017](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0017-ownership-may-be-declared-instead-of-derived.md)).
Records identify their TOML-path format with `# loadout-owned-format: 2`. An unversioned record is
read as literal top-level names, so an old dotted name cannot claim a nested setting. An unknown
record version is refused before destinations are written.

The record covers a key you stop managing. A key something else keeps writing back needs the
opposite: to stay owned and never carry a value. `$remove` says that:

```json
{
  "model": "gpt-6-astra",
  "$remove": ["developer_instructions"]
}
```

Every sync strips those keys from the destination, body and all if the value spans lines. `null`
cannot mean this: it drops the key from the fragment, which un-owns it rather than evicting it. A
key both given a value and listed in `$remove` is an error. Entries use TOML key paths, so
`"skills.max_context_tokens"` removes only that field. Parent/child ownership conflicts, including
conflicts with another slice, are refused.

### Where harness blocks write

Each destination in the preset carries that harness's config-directory variable:
`${CLAUDE_CONFIG_DIR:-~/.claude}`, `${CODEX_HOME:-~/.codex}`,
`${FACTORY_HOME_OVERRIDE:-~}/.factory`, `${XDG_CONFIG_HOME:-~/.config}/opencode` and
`${PI_CODING_AGENT_DIR:-~/.pi/agent}`. Relocating a harness is followed automatically, and with the
variable unset each resolves to that harness's own default. The paths per harness are in
[harness support](harnesses.md).

### Skill policy

A skill reaches every configured harness that consumes its source. `[skills.<name>]` narrows one,
in a global manifest and in a project's `config.toml` alike:

```toml
[skills.review]
agents = ["claude", "droid"]
render = false
```

| Key | Meaning |
| --- | --- |
| `agents` | Required. The harnesses that get the skill; in a global manifest each must have an active skills slice |
| `render` | Optional, default `true`. `false` copies the skill as written to each harness instead of rendering it per harness |

A rendered skill has its `::: <harness>` sections, `:concept[…]` and frontmatter selected per
harness (see [skills](#skills)). With `render = false`, `SKILL.md` and its supporting files are
copied byte for byte. Init writes `render = false` for every skill it adopts, so each harness keeps
reading the skill exactly as it was.

### Profiles

A profile is a file. `loadout.toml` is the default profile, and is also what marks a directory as a
loadout source. Every other profile is a sibling file beside it, and a sibling file declares its
profile whether or not any target names it:

```toml
# autonomous.toml
extends = "default"

[instructions.claude]
order = ["intro-claude", "web-fetching", "git-policy.autonomous"]
```

`extends` names a sibling profile to start from, and the file states only what differs. Profile
names cannot contain paths; profile symlinks must resolve within the same source directory, and
internal aliases use their canonical paths for cycle detection and source protection. Harness
blocks, `[all]`, and each `[instructions.<name>]` and `[permissions.<name>]` target merge per
field. Omitted fields inherit, including `output`, `destinations` and the renderer. A supplied
field replaces its complete value: lists do not append, and maps such as `substitute` replace as a
unit. `[]` means empty. A cycle in `extends` is an error naming the cycle.

Top-level `remove` deletes inherited keys before the child profile's fields apply:

```toml
extends = "default"
remove = ["instructions.claude.output"]

[instructions.claude]
destinations = ["~/.claude/CLAUDE.md"]
```

Paths use TOML key syntax, including quoted names. Missing paths, overlapping removals and paths
through lists are errors. `remove` requires `extends`. To replace a target wholesale, remove
`instructions.claude` and then declare its complete replacement. Removing a harness field exposes
any applicable `[all]` default; existing slice `false` values still disable automatic slices.

`[instructions.<name>]` and `[permissions.<name>]` targets also accept a `profile` key, which
selects the targets that render for the active profile:

- The active profile is `--profile NAME` when given, else the machine config's `profile` at the
  registered global source, else `default`.
- A target declaring `profile = X` renders only when the active profile is `X`.
- A target with no `profile` renders under every active profile, so the rest of the manifest keeps
  rendering when the machine switches to `--profile autonomous`.
- An active profile that neither a target nor a sibling file declares is an error listing the
  declared profiles. `default` is always valid.
- Two targets may share a destination only if their profiles make them mutually exclusive. A
  target with no `profile` collides with any other selected target naming the same destination,
  so to let two targets take turns writing one destination, both must declare a `profile`.

### Explicit output targets

Harness blocks use built-in destinations. A target can also be declared with its paths spelled out,
and the two forms can be mixed:

```toml
[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
profile      = "default"
order        = ["intro-claude", "web-fetching", "git-policy"]

[instructions.claude-autonomous]
output       = "claude/CLAUDE.autonomous.md"
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
profile      = "autonomous"
order        = ["intro-claude", "web-fetching", "git-policy.autonomous"]

[permissions.claude]
output = "claude/settings.json"
render = "claude"
base   = "claude/settings.base.json"

[permissions.opencode]
output   = "opencode/opencode.json"
render   = "opencode"
base     = "opencode/opencode.base.json"
preserve = ["mcp"]
```

Both instruction targets declare a `profile` because they share a destination; a target that
shares its destination with no other needs none.

| Field | Targets | Meaning |
| --- | --- | --- |
| `output` | both | Where the generated file is written, relative to the manifest root; not absolute, empty, or escaping the root with `..` |
| `destinations` | both | Where else the document is written: machine paths, with `~` expanded. `sync` and `check` write and diff each one like `output` |
| `order` | instructions | The fragment names composed into the document, in order |
| `substitute` | instructions | As in a harness block |
| `profile` | both | The profile this target renders under; see [profiles](#profiles) |
| `render` | permissions | The renderer: `claude`, `claude-mcp-permissions`, `codex`, `codex-mcp-permissions`, `droid`, `opencode` or `pi` |
| `base` | permissions | Optional JSON file, relative to the manifest root, that the renderer starts from |
| `settings` | permissions | Optional settings fragment name or ordered list |
| `preserve` | permissions | Optional top-level keys to copy forward from the file about to be overwritten |

- `render` names one of the permission renderers above. The other names in loadout's renderer
  list belong to the presets' hooks, plugins, servers and project slices; an unknown name is an
  error listing them all.
- `base` carries hand-maintained keys (model, hooks, `defaultMode`, and so on) through into the
  output untouched. It must be an existing input file, never a path that is itself a generated
  `output`
  ([0001](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0001-render-never-reads-its-own-output.md)).
- `settings` names fragments resolved as `<source>/settings/<name>.json` across every source that
  offers settings. A list composes them with a deep merge (maps merge recursively, lists
  concatenate, `null` removes a key). It is the same input as `base` by a different spelling, so
  giving both is an error. Prefer it: a profile's delta becomes
  `settings = ["claude", "claude-autonomous"]` instead of a copy of the whole document.
- `preserve` is for keys owned by some other generator, such as `mcp` in `opencode.json`. A key
  named in `preserve` must not also be one the renderer generates. Each output path is read and
  rendered on its own, so a target writing several files carries each one's foreign keys back into
  that same file.
- A target always renders the source's rules. To render one holding none, with its settings still
  around it, declare the harness in a harness block with `permissions = []`; a `rules` key is an
  error.
- No two targets, of either kind, may share an `output`, and among the targets selected for the
  active profile no two may share a destination; that is an error naming both.

Each output path is rendered separately, so the bytes are identical everywhere unless `preserve`
carries different foreign keys into different files.

Generated permission files carry no in-file marker: `claude/settings.json` (generated) and
`claude/settings.base.json` (hand-maintained, the `base`) look alike. Edit the `base` file, never
the `output`: anything put directly into a generated output is replaced at the next `sync`.

### Destination templates

A destination may read an environment variable, as `${VAR}` or `${VAR:-fallback}`:

```toml
[instructions.claude]
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
order        = ["intro-claude"]
```

This is how a destination follows a harness that keeps its config somewhere other than the
default; the [harness reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/README.md#relocating-the-config-directory)
lists the variable each harness reads. A variable that is unset or empty takes the fallback, and an
empty fallback counts as no fallback, so neither `${VAR}` nor `${VAR:-}` can resolve to nothing.

A destination is a template, resolved once per render rather than when the manifest is parsed
([0011](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0011-a-destination-follows-a-relocated-harness.md)):

- Only the targets the active profile selects are resolved, so a variable that a profile you never
  run depends on does not have to be set on this machine.
- `${...}` and `~` resolve together, and the result must be an absolute path with no `..`
  components. Two destinations that resolve to the same file collide even when their templates
  differ.
- Every substitution is textual and single-pass: a variable's value is never rescanned for further
  references.

Only `${VAR}` and `${VAR:-fallback}` are substituted. Anything else brace-shaped, such as
`${VAR-fallback}` without the colon, `${VAR:?msg}`, a nested `${A:-${B}}` or an unclosed `${`, is
an error rather than literal text. A bare `$` with no brace is left alone, so a literal `${`
cannot be expressed in a destination.

### Global source layout

```text
source/
├── loadout.toml            the default profile
├── focused.toml            another profile, optional
├── permissions.toml
├── mcp.toml
├── instructions/           <name>.md fragments
├── settings/               <name>.json fragments
├── defaults/               <name>.json Codex fragments and their <name>.owned records
├── hooks/                  <name>.json fragments
├── plugins/                <name>.json fragments
├── skills/                 <name>/SKILL.md with supporting files
├── agents/                 <name>.md subagents
├── module-config/          <harness>/<path>, copied verbatim
└── kits/                   <name>.toml manifests, <name>/ directories, and shared parts
```

Only create the directories your configuration uses. Profiles live beside `loadout.toml`; slice
directories belong under the selected source paths.

## Project configuration

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project", "testing"]
kits = ["python"]

[skills.review]
agents = ["claude"]

[kit.python]
vendored = "sha256:9f2a1c4e…"
```

| Field | Meaning |
| --- | --- |
| `harnesses` | Non-empty list of supported harness names |
| `instructions` | Ordered project instruction fragment names, from `loadout/instructions/` |
| `kits` | Ordered kit names |
| `[skills.NAME]` | `agents` and `render` for one skill; see [skill policy](#skill-policy) |
| `[kit.NAME].vendored` | Content hash maintained by `kit add --vendored` and `kit sync`; the only key it accepts |

Any other key is refused by name. The project file has no source-path list or profile
inheritance. Shared sources reach projects through kits.

### Project source layout

```text
loadout/config.toml              configured harnesses, instructions, skill policy and kits
loadout/permissions.toml         shared permission rules, optional
loadout/permissions.local.toml   your own permission rules, optional; keep it out of Git
loadout/instructions/            ordered instruction fragments
loadout/skills/                  skills, one folder each, with supporting files
loadout/agents/                  subagents, one Markdown file each
loadout/mcp.toml                 MCP server definitions, optional
loadout/hooks.json               hook declarations, for Droid
loadout/plugins.json             plugin declarations, for Droid
loadout/settings/<harness>.json  the rest of a shared settings file, for claude, droid or opencode
loadout/kits/                    vendored kits
```

A missing `permissions.toml` means the same as an empty one. `loadout/` must be a real folder, and
the files in it are read only there; see [links](../guides/projects.md#links-and-gitignore).

### Personal files

`loadout/permissions.local.toml` holds your own rules for the repository, in the
[permission rules](#permission-rules) format. It is a tier above `permissions.toml`, and the same
deny-wins merge applies, so a personal allow cannot cancel a shared deny.

`loadout/skills/<name>.local/` is a private replacement for the whole `<name>` skill: its document
and supporting files replace the shared variant together. It is the only personal skill form, and
it is not an overlay.

Loadout never adds `permissions.local.toml` to `.gitignore`: add it to `.gitignore`, or to
`.git/info/exclude` for this clone alone, yourself. Keep a `.local` skill you create out of Git the
same way.

## Stealth sources

A **stealth source** is a folder registered on this machine whose `loadout.toml` renders
personal, never-committed configuration into repositories that have not adopted loadout. The
[stealth sources guide](../guides/stealth.md) walks through one; this section is the schema.
[`loadout stealth init`](cli.md#stealth-sources) writes a minimal one and registers it.

```toml title="~/agent-config/work/loadout.toml"
[stealth]
targets = ["~/work/app*"]

[[source]]
name = "work"
path = "."

[all]
instructions = ["work"]

[claude]
settings = "local"

[codex]
[opencode]
```

It is a global-style manifest (`[[source]]`, `[all]`, one table per harness, the same validation) plus
a `[stealth]` table. A `loadout.toml` that holds a `[stealth]` table renders only when registered;
see [`stealth.toml`](#stealthtoml).

### The `[stealth]` table

| Key | Meaning |
| --- | --- |
| `targets` | Required, non-empty list of absolute or `~/` patterns. Expanded again on every run. Each match must be the top of a git checkout |
| `follow_worktrees` | Reserved for a later release. `false` or absent; `true` is refused |
| `git_hook` | Reserved for a later release. `false` or absent; `true` is refused |

Any other key is refused by name. A pattern may use `*`, `?` and `[...]`. A match that is not a
git checkout, a bare repository, a submodule or a linked worktree is skipped with a message, not
an error, so a broad pattern does not fail every run. So is a repository that has adopted loadout
(it holds `loadout/config.toml`); what the stealth source wrote there before is retired, as for a
repository the patterns stopped matching. A match that is the home folder, holds a
harness home or the stealth folder, is inside the stealth folder, or has a `.git` of its own but
is not the top of its checkout, is refused with exit 3. A pattern whose fixed parent folder
cannot be listed is reported, and its targets are neither written nor retired. Targets are
compared by file identity, so spelling and case do not matter.

### Allowed slices

A stealth source renders four slices, for every harness: `instructions`, `mcp` (server definitions),
`permissions` and `skills`. Claude and Droid also accept `settings`, which names a fragment of
the rest of their settings file, and every harness accepts `substitute`. A key in `[all]` applies to
each declared harness that allows it, and is refused only when none does.

| Named in the manifest | Result |
| --- | --- |
| `hooks`, `plugins`, `agents`, `module-config`, `defaults` | Refused: would need a destination the team's config does not merge, so a stealth source cannot write it additively |
| `mcp = [...]` | Refused, an empty list included: servers are not picked by name; the `[[source]]` entries supply them. Only `mcp = false` switches the slice off |
| Claude `mcp-permissions` | Refused: Claude's MCP approvals are in `permissions` |
| `preserve` | Refused: a stealth source owns only the keys it writes |
| `profile`, `extends`, `remove` | Refused: a stealth source has one profile, its `loadout.toml` |
| `[instructions.*]`, `[permissions.*]` | Refused: a stealth source writes only to its fixed destinations; declare harness tables instead |
| any other key | Refused: a stealth source does not render it |

A source folder that holds `agents/` or `module-config/` is not an error: the stealth source renders
neither. A stealth source renders the `default` profile only, and a slice with nothing to write writes
nothing.

### Destinations

Each destination is relative to the target's root. The files a stealth source owns whole are marked
(whole); in the three shared documents the stealth source owns only the keys and rules it renders
(see [owned documents](#owned-documents)).

| Harness | Instructions | MCP servers | Permissions and settings | Skills |
| --- | --- | --- | --- | --- |
| Claude | `CLAUDE.local.md` (whole) | `~/.claude.json`, one server at a time under `projects.<root>.mcpServers` | `.claude/settings.local.json` (shared) | `.claude/skills/<name>/` |
| Codex | `developer_instructions` in `.codex/config.toml` (whole) | `mcp_servers` in `.codex/config.toml` | `.codex/rules/<name>.rules` (whole) | `.agents/skills/<name>/` |
| Droid | `.factory/AGENTS.md` (whole) | `.factory/mcp.json` (whole) | `.factory/settings.local.json` (shared) | `.factory/skills/<name>/` |
| OpenCode | `instructions` in `opencode.json` (shared), naming a file in the stealth folder | `mcp` in `opencode.json` | `permission` in `opencode.json` | `.opencode/skills/<name>/` |
| Pi | `.pi/APPEND_SYSTEM.md` (whole) | `.pi/mcp.json` (whole) | `.pi/extensions/pi-permission-system/config.json` (whole) | `.pi/skills/<name>/` |

- Skills are rendered as at project scope. When Codex, OpenCode and Pi render a skill
  identically, one `.agents/skills/<name>/` copy serves all three.
- Codex's `.codex/config.toml` holds the instructions and the servers together, and Codex applies
  it only in a trusted project. A project `developer_instructions` replaces the user-level one. The
  rules file is written only when the stealth source has a shell rule Codex can express.
- An MCP approval for a server the stealth source does not define is not written to Codex, because a
  project table of that name would merge into the user's server.
- OpenCode's `opencode.json` is written at the repository root, where a tracked
  `.opencode/opencode.json` outranks it. The `instructions` entry names
  `<stealth folder>/.loadout/opencode-instructions.md`, a file the stealth source renders into its own
  folder and records. The `permission` object has no `"*"` catch-all unless the stealth source's
  permissions state `[shell] default`.
- Pi's permission file is written only when the stealth source has a rule or a stated `[shell] default`.
- `CLAUDE.local.md` starts with `@AGENTS.md` when the target has an `AGENTS.md` and no other
  memory file Claude would load: no `CLAUDE.md` or `.claude/CLAUDE.md` at the target or above it,
  and no `CLAUDE.local.md` above it. Claude reads `AGENTS.md` only when it finds none of these, and
  the stealth source's own `CLAUDE.local.md` is one. Claude's user memory file does not count.
- A path the team tracks is never written. `CLAUDE.local.md`, `.claude/settings.local.json` and
  `.factory/settings.local.json` can be ignored in every repository; see
  [stealth sources](../guides/stealth.md#what-stays-out-of-git).

### Owned documents

`.claude/settings.local.json`, `.factory/settings.local.json` and `opencode.json` often exist
already and hold keys that are not the stealth source's. The stealth source owns two kinds of part, and nothing
else in the file is judged, written or retired:

| Document | Owned keys | Owned rules |
| --- | --- | --- |
| `.claude/settings.local.json` | The keys of the `settings` fragment, at the top level and under `permissions` except `allow`, `ask` and `deny` | The entries it writes into `permissions.allow`, `permissions.ask` and `permissions.deny` |
| `.factory/settings.local.json` | The keys of the `settings` fragment, except the three command lists | The entries it writes into `commandAllowlist`, `commandDenylist` and `commandBlocklist` |
| `opencode.json` | `permission`, `mcp` and `instructions` | none |

- **Saved approvals are kept.** A rule already in a list that the stealth source did not write, such as a
  Claude "don't ask again" approval, stays after the stealth source's own entries, and `check` lists it
  as `saved by the harness, not in the stealth source: <rule>; add it to the stealth source's
  permissions.toml or delete it`. The same applies to a command-list entry Droid saved. It is not
  drift and never blocks `sync`. A rule that holds a secret, such as a token in a `curl` header,
  is listed as `a rule not shown because it holds a secret (<reason>)`; read the file for it.
- **A rule the stealth source no longer renders is removed.** So is an owned key the source stopped
  setting, so a removed rule or setting never comes back from disk.
- **A key the stealth source would write that already holds a different value, and was not written by the
  stealth source, is refused**, naming it, with exit 3 and even under `--force`. The same value is adopted.
- **An owned key edited outside loadout** is `owned-key`; a key, or a rule that is still rendered,
  that was removed is `owned-key-removed`. Both are refused with exit 1 until `--force`. An owned
  rule that was edited reads as a removed rule plus a saved approval, so `--force` restores the
  original and keeps the edited text; delete the edited line yourself.
- **A file is rendered only when the stealth source has content for it.** Saved approvals alone never
  make the stealth source take a file over.
- **Retirement strips the owned parts.** The file is deleted only when the stealth source created it and
  nothing is left but `{}`. When a whole target is retired, a file left behind is also taken out
  of the stealth source's `info/exclude` block, so `git status` shows what is left in it.

Every other destination is a whole file the stealth source owns. If one already exists at that path and
loadout never wrote it, `sync` refuses it as `unknown` (exit 1) and `sync --force` replaces it. The
stealth source's lines in `.git/info/exclude` then hide the replaced content from git, so copy it first.

### `stealth.toml`

Stealth sources are registered in `$XDG_CONFIG_HOME/loadout/stealth.toml` (`~/.config/loadout/` when
unset), beside the machine config but separate from it. `loadout stealth add` and
`loadout stealth remove` manage it; edit it by hand only to repair it.

```toml title="~/.config/loadout/stealth.toml"
[stealth.work]
path = "/Users/me/agent-config/work"
```

Tables are sorted by name, which matches `[a-z0-9][a-z0-9-]{0,62}`. `path` is absolute and
resolved when registered. `removing = true` is written while a `stealth remove` is unfinished, and
`sync` and `check` then refuse that stealth source. A missing file registers nothing. Any other key, a
folder registered twice, a bad name or a relative path is an error naming the file, and so is a
file that does not parse. Every `sync` and `check` reports that error with exit 3 and still runs
the global source or the project. A file that cannot be read is a warning in a project instead. What a
stealth source wrote is recorded in `$XDG_CONFIG_HOME/loadout/stealth-written/`, one file per stealth source
folder; see [the sync reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md#stealth-sources).

## Skills

A skill is a folder holding `SKILL.md`, with supporting files beside it, in `skills/` in a global
source or `loadout/skills/` in a project:

```text
loadout/skills/review/SKILL.md
loadout/skills/review/reference.md
```

That is the only skill form. A skill's folder name, like an agent's file name, may not hold a
control character such as a line break, or a `/`, or be `.` or `..`, since it becomes a path and a
`.gitignore` line. A file directly in `skills/` is not a skill: `README.md` and dotfiles are skipped
silently, and any other loose file is skipped with a notice from `sync` and `check`.

Loadout renders `::: <harness>` sections and `:concept[…]` separately for each selected harness.
Frontmatter that only Claude reads (`model`, `effort`, `context` and `agent`) goes into Claude's
copy only; a harness block such as `droid:` with `model: …` under it sets a key for that harness.
A skill with `agent: reviewer` hands its work to the `reviewer` subagent from `agents/`: Claude's
copy also gets `context: fork`, and every other harness's copy opens with "Delegate this task to
the `reviewer` subagent." Naming an agent the source does not define is an error for a rendered
skill.

A kit contributes `skills/` as a tier beneath the project, so a skill the project defines
under the same name replaces the kit's.

## Subagents

A subagent is one Markdown file, `agents/<name>.md` in a global source or `loadout/agents/<name>.md`
in a project, and it reaches every configured harness: Claude, Codex (as `<name>.toml`), Droid
(as a custom droid), OpenCode and Pi (read by the `pi-subagents` package, which Pi needs installed).

```markdown
---
description: Reviews a diff for defects.
model:
  claude: sonnet
  codex: gpt-5.5
  opencode: anthropic/claude-sonnet-5-5
effort: high
claude:
  tools: Read, Grep
---

Review the staged diff.
```

The body is the agent's instructions, with `::: <harness>` sections as in a skill. The file name is
the agent's name; a `name` field must match it. `model` is `inherit` or a map per harness, and a
harness it leaves out inherits; one name for every harness is refused, since no model name means
the same thing in all five. `effort` is one word from `off` to `max`, translated per harness, or a
map. A harness lacking the level gets the nearest lower one, and OpenCode none, each with a note.
A block keyed by a harness holds what only that harness reads; Droid's `droid:` block takes only
`tools`, `mcpServers` and `version`, since Droid drops any other key when it rewrites its droids
folder. Droid's copy is written in the form Droid itself writes, so creating or editing a droid in
`/droids` leaves it unchanged. Removing or renaming the file retires the old agent everywhere. Details:
[models](https://github.com/nielsmadan/loadout/blob/main/docs/reference/models.md#what-loadout-does).

## MCP server definitions

`mcp.toml` declares servers, at the root of a global source or as `loadout/mcp.toml` in a project:

```toml
[jina]
transport = "http"
url = "https://mcp.jina.ai/v1"
auth_env_var = "JINA_API_KEY"      # the NAME of an environment variable, never a token

[context7]
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
```

`transport` is `"http"` (needs `url`, optionally `auth_env_var`) or `"stdio"` (needs `command`,
optionally `args` and `env`); anything else is refused at parse time. `auth_env_var` names a
variable; the value it holds never reaches a rendered file.

This is distinct from `[mcp]` in `permissions.toml`, which controls which of a server's tools may
be called, not which servers exist. Where each harness receives servers is in
[harness support](harnesses.md); full detail is in
[servers](https://github.com/nielsmadan/loadout/blob/main/docs/reference/servers.md).

## Kits

A project names kits in `config.toml` (`kits = [...]`), by name and never by path. A name
resolves to `loadout/kits/<name>/` or `loadout/kits/<name>.toml` when the project vendored
it, then to the `kits/` folder of a source the machine's global manifest declares, and finally
to the bundled `frontend` or `backend` kit. With no machine config, the bundled kits come
first. Two sources offering one name is an error unless it is qualified as `source/name`. The bundled kits ship in the package and work
offline; they add no dependencies, permission grants, models or credentials.

Kits contribute instructions, skills, MCP servers and permissions through the same project
slices as directly authored source, as a tier beneath the project, so anything the project
declares outranks them. A vendored copy is source, not generated output: `check` reports one you
edited without failing, and `kit sync` refuses to overwrite it
([0014](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0014-a-vendored-template-is-source-not-output.md)).

In a global source, a kit is either a directory, `kits/<name>/`, holding its own files
such as `instructions.md`, `permissions.toml`, `mcp.toml` and `skills/`, or a manifest,
`kits/<name>.toml`, that selects shared parts:

```toml
# kits/nextjs.toml
skills = ["review-typescript"]
instructions = ["typescript", "nextjs"]
mcp = ["github"]
permissions = ["node"]
```

Parts live under `kits/skills/`, `kits/instructions/`, `kits/mcp/` and
`kits/permissions/`, separate from the source's active folders, and several manifests can
reference the same part. Vendoring keeps one shared copy of a part; an update to a shared part
lists every affected kit and asks first, and a locally modified copy blocks it. See [kits](../guides/kits.md) and the
[kits reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/kits.md).

0.10.0 called a kit a template. Its spellings, `templates = [...]`, `[template.NAME]`,
`loadout/templates/`, a source's `templates/` folder and `templates` in `use`, are refused with an
error naming the new one
([0053](https://github.com/nielsmadan/loadout/blob/main/docs/decisions/0053-overlays-become-stealth-sources-and-templates-become-kits.md)).

## Permission rules

```toml
[shell]
default = "ask"
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]

[mcp]
allow = ["docs/search"]
ask = ["docs/update"]
deny = ["docs/delete"]
```

- Shell entries are command strings. The renderer adapts their representation to each harness.
- MCP entries use `server/tool` or `server/*`.
- `allow`, `ask`, and `deny` are lists; omitted lists are empty.
- Any other table or key is refused by name, so a typo such as `[shel]` stops the sync instead of
  dropping its rules. `[opencode.extra]` takes any key.
- The strictest decision wins when the same entry appears in several tiers.
- An omitted shell default casts no vote in composition. An explicitly stated default does.
- `[shell] default` is rendered for OpenCode and Pi. Claude and Codex use native settings;
  Droid uses session autonomy. `loadout sync` names the targets a stated default does not reach.
  `default = "allow"` with a deny list is weaker than it looks; see
  [the catch-all default](https://github.com/nielsmadan/loadout/blob/main/docs/reference/README.md#the-catch-all-default).
- A bare `"*"` shell entry is invalid. Use the `default` key for a catch-all.
- A plain shell entry is a prefix: `"git status"` also matches `git status --short`. An entry
  starting with `=` is exact: `"=git status"` matches that command and nothing longer. An exact
  entry may not contain `*`.

Exact entries render exactly for Claude, OpenCode, and Pi. Codex and Droid cannot match a whole
command, so Loadout omits an exact `allow` or `ask` for them, leaving the command to their own
approval, and renders an exact `deny` as its prefix, which blocks more rather than less.
`loadout sync` reports each one.

Trailing shell globs can be represented by Claude, OpenCode, and Pi. Loadout skips them for
Codex's literal prefix rules and Droid's literal permission lists. Different matching behavior remains visible in the
[harness reference](harnesses.md#permission-portability).

The same format serves a global source's `permissions.toml`, a project's `loadout/permissions.toml`,
a kit's `permissions.toml`, and a project's personal `loadout/permissions.local.toml`. In a
project the tiers are kit, shared and personal, merged with deny winning.

### Harness-specific entries

Native additions can be carried alongside portable rules:

```toml
[claude.extra]
allow = ["Read(//tmp/**)"]

[opencode.extra]
webfetch = "allow"
```

These entries are specific to their harness. Keep portable rules in the shared shell/MCP
sections whenever those sections express the behavior you need.
