<p align="center"><img src="docs/assets/logo.svg" alt="Loadout backpack" width="140"></p>

# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

**Switch agents. Keep your setup.** Reuse project templates and compose shared and personal
configuration from one source.

[Documentation](https://loadoutai.dev/) · [Getting started](https://loadoutai.dev/getting-started/)
· [Templates](https://loadoutai.dev/guides/templates/)

To preview, build, or publish the site, see [Publishing the documentation](docs/publishing.md).
For versioned packages and Homebrew distribution, see [Releasing Loadout](docs/releasing.md).

## Install

    just install

This installs a snapshot of the current checkout onto `PATH`. Run it again after changes
to replace the installed code, even when the package version is unchanged.
Use `just install-editable` to have source edits take effect without reinstalling.
`just uninstall` removes the CLI installation and preserves configuration and generated files.

## Use

    loadout sync                  # regenerate generated files under the current repo
    loadout sync --global         # regenerate this machine's global configuration
    loadout sync --profile NAME   # regenerate under a specific active profile (see Profiles below)
    loadout sync --force          # regenerate even over files modified outside loadout
    loadout check                 # exit 1 if any generated file has drifted; the preview of sync
    loadout check --json          # the same, as JSON (not covered by a stability promise in 1.0)
    loadout check --global        # check drift in this machine's global configuration
    loadout check --profile NAME  # check drift under a specific active profile

## Bundled skill

Install the version-matched `loadout` skill into the configured global Loadout source:

    loadout integrate skill install          # show the source path and confirm
    loadout integrate skill install --yes    # non-interactive
    loadout integrate skill status
    loadout integrate skill uninstall        # remove the owned source copy and generated outputs

The command reads the machine config and active profile. Legacy manifests vendor a normal
`skills/loadout/` source. An existing copy is selected using the same declared overrides as
rendering; `--source NAME` must identify that active source. When no copy exists and several
declared sources offer skills, choose the installation source with `--source NAME`.
Native manifests install into every active skill-directory tree route, deduplicating shared source
paths and reporting each route's consumers. `--profile NAME` overrides the active profile.

An ownership marker records the installed content hash. Legacy markers stay inside the source
copy and are excluded by the skill renderer; native markers live in `.loadout-bundles/` outside
deployable trees. Native installation work and recoverable originals stay under private,
gitignored `.loadout-state/`. Reinstalling refreshes an unchanged older copy. A source copy you edited, an unowned
skill with the same name, or a generated output edited outside Loadout is reported and left alone.
Uninstall removes only the owned source and unchanged files rendered from it; unrelated files in
the destination directory survive.
When uninstalling an overriding copy, the command also removes its `loadout` override entry from
the manifest that declares it, preserving other entries, comments and file mode. The earlier
source's skill becomes active again and the command's sync deploys it. An inherited declaration
is updated in its declaring parent, affecting every profile that inherits those sources.
Native changes preflight all selected destinations, roll back failed changes while their postimages
still match, and update normal deployment receipts. A rollback conflict retains originals and
`recovery.json` in the reported private work directory for explicit reconciliation.
Mixed native/legacy installs recheck frozen legacy output bytes, modes and absences immediately
before each write or removal; concurrent edits are preserved and earlier changes roll back.
If a native source copy disappears, reinstall repairs it using its unchanged external ownership
marker; uninstall removes that marker. Invalid or changed ownership metadata blocks the operation.

Invoke the installed skill as `loadout` using the harness's skill syntax. Configuration defaults
to personal rules for the current project; use `--project` for committed repository configuration
or `--global` for machine-wide configuration. The skill edits Loadout sources, applies generic
requests to configured agents that support them, and runs the matching sync command. Restart an
already-running agent session after first installation so it refreshes its skill catalog.

A fragment name may be qualified as `source/name`. Loadout refuses an unqualified name that more
than one source declares, naming the candidates so you can pick one. Instruction fragments are
global scope only — they are not part of project scope (see below).

### Files modified outside loadout

Generated files look like ordinary files, so they get hand-edited — and the edit is then lost
on the next `sync`, silently. Before writing, `sync` compares each file against every output
loadout itself could have produced: rendered from the committed source and from the working
tree, under every declared profile. A file matching none of them was written by something
else, so `sync` names it, changes nothing, and exits 1. It prints a diff alongside the name —
the `-` lines are the ones that exist only on disk, so a permission a harness granted itself at
runtime is reported verbatim rather than silently discarded. Move the edit into the source, or
pass `--force` to discard it.

Comparison is by parsed document for JSON targets, not by bytes, so a harness re-serialising
its own config in a different key order does not read as an edit.

There is a third comparison beside those two renders: **what `sync` recorded writing there
last**, kept per destination under `$XDG_CONFIG_HOME/loadout/written/`. Without it, editing a
source twice before committing left an output rendered from a state that is neither baseline,
and `sync` refused a file it had written itself. The record only ever widens acceptance — delete
it and the guard behaves exactly as it did before, warning and all.

The guard does not need git. Outside a repository, or before the first commit, the working
source and the record are the baseline: an unsynced source edit leaves the output matching what
was recorded, while a hand edit does not. On the very first `sync`, with neither a commit nor a
record, a file that does not match the working source is refused until you pass `--force`.

Two cases warn without anything being wrong. The first `sync` over config that predates
loadout — adoption is the one time loadout overwrites a file it has never written. And a
destination shared between machines through a symlinked config repo: one machine's record cannot
vouch for the other's write.

`check` asks the same question and says which answer applies, rather than sending you to `sync`
where `sync` is about to refuse.

## Shell completions

    loadout integrate completions install            # for the shell running loadout
    loadout integrate completions install --shell fish
    loadout integrate completions status
    loadout integrate completions uninstall

Completes subcommands and fixed-choice options for bash, zsh and fish. The script is written to
the shell's own completion directory and never into a startup file; `install` prints the one line
to add where the shell needs it. `status` says whether the installed script is current, and
`uninstall` removes only a script loadout wrote. Locations are in the
[CLI reference](docs/user/reference/cli.md#shell-completions).

## Global scope

Global scope is the configuration that applies to every project on this machine. A **machine
config** says where its source lives:

```toml
# $XDG_CONFIG_HOME/loadout/config.toml, or ~/.config/loadout/config.toml
source    = "~/ac"                    # directory holding loadout.toml; must exist
profile   = "autonomous"              # optional; active profile, default "default"
harnesses = ["claude", "codex", "pi"] # optional; defaults for fresh project init
```

`source`, `profile`, and `harnesses` are the only accepted keys — anything else is an error, so a
typo fails loudly. An explicit `--harness` selection overrides the machine default. Existing
projects keep the harnesses in their own config. The file is machine state: never
version-controlled, never generated, and the only place loadout *stores* state that is not part
of a source (see
[0010](docs/decisions/0010-a-machine-config-locates-the-global-source.md),
[0021](docs/decisions/0021-global-init-records-project-harness-defaults.md), and
[0008](docs/decisions/0008-generated-files-carry-no-machine-state.md)). It is not the only
machine state loadout *reads*: a destination template resolves environment variables at render
time, per [0011](docs/decisions/0011-a-destination-follows-a-relocated-harness.md).

    loadout init global --dry-run --json
    loadout init global /work/dotfiles --harness claude --harness pi --yes

`init` finishes with the first render. It compares what loadout would write with what is on disk, writes it and says so when the two match, and shows the diff before applying when they do not. A `sync` straight after `init` changes nothing. It records what it wrote, so the first `sync` has a baseline.

Global init discovers existing configuration in live harness roots and the selected directory,
which defaults to cwd. It migrates into `<source>/loadout/`, with explicit native category routes
and private sources where required, and registers that actual manifest directory and resolved
harness list. That list becomes the default for fresh project init. An existing Loadout manifest
is recognized as source and left intact; repeat init reports no migration while refreshing the
saved harness default.
A conflicting machine registration requires `--registration replace` or `--registration keep`.
`--yes` approves resolved operations;
it never chooses between conflicting sources.

Use `--mapping` and `--select-source` JSON arguments for unusual layouts or duplicate copies.
The [migration reference](docs/reference/migration.md#cli-workflow) documents their exact shape.
Unresolved previews exit 2 without mutation. Supported configurations get a read-only preview of
checkpoint paths, source/output writes, private exclusions and removals before approval. Applying
checkpoints eligible originals through Git, migrates and syncs, then stages the final source and
ignore changes. Successful init and conflict-free recovery remove temporary journals and backups;
interrupted transactions retain them for resume or recovery. Deployment receipts remain for sync.
The final migration is not automatically committed. Choose a dedicated directory;
new repositories at HOME or the filesystem root are refused.

A missing machine config is **not** an error — it means this machine has no global scope,
which is correct for someone who only uses project scope. `loadout sync --global` without one
fails and names the file to create. `loadout init project` notes its absence and carries on.

For `sync` and `check`, `--global` and `--root` are mutually exclusive; `--global` resolves the root from the machine
config. `--profile` still wins over the machine config's `profile` when both are given.

## `loadout.toml`

A repo's root `loadout.toml` — the global source's manifest, or a project's — declares the
sources fragments come from and the instruction files to render from them:

```toml
[[source]]
name = "ac"
path = "."
# use = ["instructions"]   # optional: restrict which artifact types this source contributes

[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
profile      = "default"
order        = ["intro-claude", "web-fetching", "git-policy"]

[instructions.claude-autonomous]
output       = "claude/CLAUDE.autonomous.md"
destinations = ["~/.claude/CLAUDE.md"]
profile      = "autonomous"
order        = ["intro-claude", "web-fetching", "git-policy.autonomous"]
```

(Both declare a `profile` here because they share a `destination` — see Profiles below. A
target that doesn't share its destination with anyone else doesn't need one.)

### Several sources

`[[source]]` is an ordered list. Permissions merge every contributing `permissions.toml`;
MCP definitions use the last source for each server name. Named fragments require an unambiguous
name or a `source/name` qualifier. Skills and module files require an explicit declaration to
override an earlier source:

```toml
[[source]]
name = "company"          # a repo you cloned — loadout never fetches it
path = "~/src/acme-loadout"

[[source]]
name = "me"
path = "."

[source.overrides]
skills = ["review"]
module-config = ["pi/extensions/status/config.json"]
```

Each override selects the replacing source's complete skill tree or module file. It must name
an item that this source offers and an earlier source also offers when the collection is rendered;
undeclared collisions remain errors. Module paths include the harness name. An override category
must participate in that source's `use`. See [composition rules](docs/reference/composition.md).

Global skills target every configured agent that consumes the source by default. A manifest policy
can narrow one skill:

```toml
[skills.review]
agents = ["claude", "droid"]
```

Merging is union with **deny wins**: a deny in any source beats an allow in any other, whichever
order they appear in. Order still matters for *emission* — OpenCode and Pi resolve last-match-wins
— so entries from earlier sources are emitted first.

`[shell] default`, the verdict for everything no rule matches, resolves the same way: the
strictest value any source **states** wins. A source that omits the key casts no vote, so it
never tightens one that set it. Only OpenCode and Pi carry it in a document loadout authors;
`loadout sync` names the targets a stated default does not reach. See
[the catch-all default](docs/reference/README.md#the-catch-all-default) and
[ADR 0016](docs/decisions/0016-a-catch-all-is-stated-only-where-the-document-carries-it.md) —
including why `default = "allow"` plus a deny list is weaker than it looks.

loadout does not fetch, version or distribute a source. Getting the company repo onto your disk
is git's job.

Each `[[source]]` is a named directory containing an `instructions/*.md` tree that fragments
are pulled from. Each `[instructions.<agent>]` table declares one generated file: `output` is
where it is written, relative to the repo root (it may not be absolute, empty, or escape the
root with `..`), `order` is the ordered list of fragment names composed into it, and
`destinations` is where else the document is written — real paths on the machine, such as
`~/.claude/CLAUDE.md`, with `~` expanded to the user's home directory. `sync` and `check` write
and diff every destination exactly like the in-repo `output`. Each output path is rendered
separately, so the bytes are identical everywhere unless `preserve` (below) carries different
foreign keys into different files.

A destination may also read an environment variable, as `${VAR}` or `${VAR:-fallback}`:

```toml
[instructions.claude]
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
order        = ["intro-claude"]
```

This is how a destination follows a harness that has been told to keep its config somewhere
other than the default — see [docs/reference](docs/reference/) for the variable each harness
reads. A variable that is unset **or empty** takes the fallback, and an empty fallback counts
as no fallback, so neither `${VAR}` nor `${VAR:-}` can quietly resolve to nothing.

**A destination is a template, resolved once per render rather than when the manifest is
parsed.** Three consequences:

- Only the targets the active profile selects are resolved, so a variable a
  profile you never run depends on does not have to be set on this machine.
- `${...}` and `~` resolve together, and the result must be an **absolute path with no `..`
  components** — a relative one would be written under whatever directory `loadout` happened
  to be run from. Two destinations that resolve to the same file collide even when their
  templates differ.
- Every substitution is textual and single-pass: a variable's *value* is never rescanned for
  further references.

Only `${VAR}` and `${VAR:-fallback}` are substituted. Anything else brace-shaped —
`${VAR-fallback}` without the colon, `${VAR:?msg}`, a nested `${A:-${B}}`, an unclosed
`${` — is an **error**, not literal text, because silently leaving it in the path is how a
template ends up being created as a directory. A bare `$` with no brace is left alone, so a
literal `${` cannot be expressed in a destination.

`[permissions.<name>]` targets accept `destinations` the same way. Structured manifests require
at least one source, and at least one `[instructions.<agent>]` or `[permissions.<name>]` target must be
declared; no two targets, of either kind, may share an `output` path, and — among the targets
selected for the active profile — no two may share a `destination` either; that raises a
`LoadoutError` naming both.

### Agent blocks

An agent block names a harness and the slices it takes. Destinations come from a built-in preset,
so a manifest never spells out a machine path:

```toml
[claude]
instructions = ["intro-claude", "web-fetching", "git-policy"]
settings     = "claude"

[codex]
[pi]
```

`[codex]` and `[pi]` with no keys are complete declarations. **`permissions`, `mcp`, `skills`
and `module-config` render without being asked for**, because none has an authoring decision to
make — for the last two the directory is the declaration, so dropping a tree in renders it. Say
`skills = false` or `module-config = false` to opt out; an absent key means *automatic*. `instructions` must
be named — it needs an order, and alphabetical is wrong (see Profiles). `settings` must be named
because it is an input rather than an output.

`hooks` and `plugins` must be named too, and for the same reason as `instructions`: which hooks
run and which plugins are on are authoring decisions, so an absent key means *loadout does not
manage this*, not *none*. Both take fragment names, resolved as `<source>/hooks/<name>.json` and
`<source>/plugins/<name>.json` and deep-merged in order — maps merge key by key, lists
concatenate, and `null` removes, which is how a profile switches one plugin off with a one-key
overlay instead of restating a list. See [docs/reference/plugins.md](docs/reference/plugins.md)
for what a plugin reference holds and what each harness makes of it.

Pi's plugin declaration shares `settings.json` with `lastChangelogVersion`, a cursor Pi updates
after upgrades. The built-in Pi preset preserves that live key automatically; it does not belong
in a settings fragment and loadout refuses a fragment that tries to manage it.

`module-config` carries a module's *own* content — Pi's `pi-statusline.json`, a Claude hook
script the `hooks` slice names by path, an OpenCode plugin's `.ts` file. Files under
`<source>/module-config/<agent>/` are copied verbatim to the same relative path
beneath that harness's config directory, keeping the module's formatting and any executable bit.
The path is authored rather than derived because it has to be: `pi-subagents` reads
`extensions/subagent/config.json`. See
[docs/reference/module-config.md](docs/reference/module-config.md).

`defaults` (Codex only) must be named for a stronger reason: it manages **selected keys of
`~/.codex/config.toml`**, a file loadout does not own, and it strips every key it manages. A
machine that never asked for it must never have its hand-maintained Codex settings touched, so
absence means *loadout manages none of them*. It takes fragment names resolved as
`<source>/defaults/<name>.json`, whose objects become TOML tables:

```toml
[codex]
defaults = "codex"        # loadout/defaults/codex.json → model, model_reasoning_effort, …
```

Nested objects own their leaf fields, not the entire parent table. For example, this fragment
sets the available-skills catalog budget without managing other `skills` settings:

```json
{
  "skills": {
    "max_context_tokens": 10000
  }
}
```

It renders `max_context_tokens = 10000` under `[skills]` and preserves `[[skills.config]]` overrides.
An empty object owns nothing. Arrays are owned as a whole field, not element by element.
Use nested JSON objects, not a literal `"skills.max_context_tokens"` JSON key: the latter is
a distinct, quoted TOML key. See [nested Codex defaults](docs/reference/codex.md#nested-defaults).

Because the key names are yours rather than a set loadout could enumerate, that slice keeps an
owned-key record beside its fragment — `loadout/defaults/<name>.owned`, generated and committed.
It is what lets *removing* a key from the fragment remove it from `config.toml`, instead of
stranding it there with nothing able to say it was ever managed. Edit the fragment, never the
record; `loadout check` reports a record that disagrees with it. See
[0017](docs/decisions/0017-ownership-may-be-declared-instead-of-derived.md).

New records identify their TOML-path format with `# loadout-owned-format: 2`. Unversioned
records are migrated as literal top-level names, so an old dotted name cannot claim a nested
setting. Unknown record versions are refused before destinations are written.

The record covers a key you *stop* managing. A key something else keeps writing back needs the
opposite — stay owned forever, and never carry a value. `$remove` says that:

```json
{
  "model": "gpt-6-astra",
  "$remove": ["developer_instructions"]
}
```

Every sync strips those keys from the destination, body and all if the value spans lines. There
is no value that could mean this: `null` is already taken, and `merge_documents` reads it as
*drop from the fragment*, which un-owns the key rather than evicting it. A key that is both
given a value and listed in `$remove` is an error — one says write this, the other says write
nothing. Entries use TOML key paths, so `"skills.max_context_tokens"` removes only that field.
Parent/child ownership conflicts, including conflicts with another slice, are refused.

An unknown agent name, or a slice an agent does not offer, is an error listing what is available.

Each destination in the preset carries that harness's config-directory variable —
`${CLAUDE_CONFIG_DIR:-~/.claude}`, `${CODEX_HOME:-~/.codex}`,
`${PI_CODING_AGENT_DIR:-~/.pi/agent}`, `${XDG_CONFIG_HOME:-~/.config}/opencode` — so relocating a
harness is followed automatically. With the variable unset each resolves to that harness's own
default, so the preset changes nothing on a machine that has relocated nothing.

The `[instructions.<agent>]` / `[permissions.<name>]` spelling below still works and can be mixed
with agent blocks during the transition.

### Profiles

**A profile is a file.** `loadout.toml` *is* the default profile, and is also what marks a
directory as a loadout source. Every other profile is a sibling beside it:

```toml
# autonomous.toml
extends = "default"

[instructions.claude]
order = ["intro-claude", "web-fetching", "git-policy.autonomous"]
```

`extends` names a sibling profile to start from, and the file states only what differs. Profile
names cannot contain paths; profile symlinks must resolve within the same source directory.
Internal aliases use their canonical paths for cycle detection and source protection. Agent blocks,
`[all]`, and each legacy instruction/permission target merge **per field**. Omitted fields inherit,
including `output`, `destinations` and the renderer. A supplied field replaces its complete value:
lists do not append, and maps such as `substitute` replace as a unit. `[]` means empty. A cycle in
`extends` is an error naming the cycle.

Top-level `remove` deletes inherited keys before applying the child profile's fields:

```toml
extends = "default"
remove = ["instructions.claude.output"]

[instructions.claude]
destinations = ["~/.claude/CLAUDE.md"]
```

Paths use TOML key syntax, including quoted names. Missing paths, overlapping removals and paths
through lists are errors. `remove` requires `extends`. To replace a target wholesale, remove
`instructions.claude` and then declare its complete replacement. Older child targets that relied
on omission to discard parent fields should use this spelling. Removing an agent field exposes
any applicable `[all]` default; existing slice `false` values disable automatic slices.

**`[all]` supplies defaults to the agents you declared**, so shared configuration is written
once:

```toml
[all]
instructions = ["intro-shared", "web-fetching.shared"]

[codex]
[pi]
```

It does not *declare* agents — an agent still has to be named, or adding `[all]` would silently
enable every harness. A default an agent has no slice for is ignored rather than an error, since
`[all]` applies where it applies; a key an agent block names itself is still checked.

**`substitute` swaps one fragment for another** without restating a list:

```toml
[claude]
substitute = { git-policy = "git-policy.autonomous" }
```

Nothing is inferred from a filename — `git-policy.autonomous.md` is just a name, and the swap is
declared where it applies.

`substitute` also works in `[instructions.<name>]` targets.

The older spelling below still parses, so both work during the transition.

Both `[instructions.<agent>]` and `[permissions.<name>]` targets accept an optional `profile`
key, as `instructions.claude-autonomous` does above. It selects which targets render for the
machine's **active profile**:

- The active profile is the literal string `"default"` unless overridden with `--profile NAME`
  on `sync` or `check`.
- A target declaring `profile = X` renders only when the active profile is `X`.
- A target with **no** `profile` renders under every active profile. This is what keeps the
  rest of the manifest — `instructions.shared`, and every `[permissions.<name>]` target that
  doesn't itself declare a profile — rendering unchanged when the machine switches to
  `--profile autonomous`; only the targets that opt into a profile are gated by it.
- An active profile that no target declares is a `LoadoutError` listing the profiles that are
  declared, except `"default"`, which is always valid even if nothing declares it.
- Two targets may share a `destination` only if their `profile`s make them mutually exclusive.
  A target with no `profile` is selected under every active profile, so it collides with *any*
  other selected target naming the same destination — including a profiled one. To let two
  targets take turns writing one destination, both must declare a `profile` (e.g. `"default"`
  and `"autonomous"`), not just one of them.

### `[permissions.<name>]`

A source that declares `use = ["permissions"]` (or omits `use` entirely) may also provide a
`permissions.toml` rule file. Each `[permissions.<name>]` table renders that rule
file through one named renderer into one generated file:

```toml
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

- `output` — where the generated file is written, relative to the repo root. Subject to the
  same rules as an instructions target's `output`.
- `render` — the renderer name. One of: `claude`, `claude-mcp-permissions`, `codex`,
  `codex-mcp-permissions`, `pi`, `opencode`.
- `base` — optional. A JSON file, relative to the repo root, that the renderer starts from —
  hand-maintained keys in it (model, hooks, `defaultMode`, and so on) are carried through into
  the output untouched. A `base` must be an existing input file; it may never point at a path
  that is itself a generated `output` (that would reintroduce reading a renderer's own prior
  output as its template, which this design deliberately avoids).
- `settings` — optional. Fragment name(s) of the **settings slice**, resolved as
  `<source>/settings/<name>.json` across every source that offers settings. A string names one
  fragment; a list composes them in order with a deep merge (maps merge recursively, lists
  concatenate, `null` removes a key). This is the same input as `base` by a different spelling,
  so giving both is an error. Prefer it: expressing a profile's delta as
  `settings = ["claude", "claude-afk"]` replaces copying a whole document to change one key.
- `preserve` — optional list of top-level keys to copy forward out of the file about to be
  overwritten, for keys owned by some other generator (for example, `mcp` in `opencode.json`,
  owned by the MCP sync). A key named in `preserve` must not also be one the renderer generates.
  Each output path is read and rendered on its own, so a target writing several files carries
  each one's foreign keys back into that same file — a co-owner that writes only the machine
  destination keeps its key there, and needs no staged copy in the repo to write into.
- `rules` — optional; the only accepted value today is `[]`, meaning "select nothing" — the
  target renders with all rule categories empty. Named rule-set selection is not implemented yet.

**Which file do I edit?** Generated permission files carry no in-file marker of that fact —
`claude/settings.json` (generated) and `claude/settings.base.json` (hand-maintained, the
`base`) look identical at a glance. Edit the `base` file, never the plain `output` file:
anything you put directly into a generated output is silently discarded at the next
`loadout sync`.

## Project scope

Per-repo permissions and instructions, layered on top of the global manifest above. A repo opts
in once:

    loadout init project --harness claude --harness opencode --dry-run
    loadout init project --harness claude --harness opencode --yes

`init` finishes with the first render. It compares what loadout would write with what is on disk, writes it and says so when the two match, and shows the diff before applying when they do not. A `sync` straight after `init` changes nothing.

To start from a small instruction seed, apply a bundled template after init with
`loadout template add frontend --vendored` (or `backend`), then `loadout sync`. Bundled templates
ship in the package and work offline. They add no dependencies, permission grants, models or
credentials.

Git hooks are not part of adopting configuration: install them with
`loadout integrate git-hooks install --root . [--regenerate]`, which reports exact integration
commands and preserves existing or shared hooks. `--regenerate` also runs guarded sync after
checkout and merge. A new clone needs that command explicitly. Manual validation is
`loadout check --staged`.
See [Git integration](docs/reference/git-integration.md) for dependencies, alternate indexes,
worktrees, profiles and post-hook failure behavior.

`init` adopts only configuration with a known slice destination. It extracts permissions,
instructions, settings, hooks, plugins, MCP servers, skills and global module configuration into
canonical sources. Harness-specific command and subagent trees stay where their harness reads them;
Loadout neither moves nor ignores them.

A project source normally contains:

```text
loadout/config.toml          configured harnesses, instructions and templates
loadout/permissions.toml     shared permission rules, optional
loadout/instructions/        ordered instruction fragments
loadout/skills/              authored skills and supporting files
loadout/mcp.toml             MCP server definitions, when present
loadout/hooks.json           hook declarations, when supported
loadout/plugins.json         plugin declarations, when supported
```

A missing `permissions.toml` means the same as an empty one. The harnesses you declare are what
make loadout own their permission keys, not the file's existence, so without it `sync` writes each
key empty and clears whatever rules were there. Deleting the file clears your rules on the next
sync, just as emptying it does. Rules loadout did not write are refused first; see
[Files modified outside loadout](#files-modified-outside-loadout).

Generated project files are ignored and removed from the final index while remaining available to
the harness. Re-running init does not reinterpret an existing source; edit it and use `check` or
`sync`. Repositories adopted by an older route-based build should restore any command or subagent
files from `loadout/` to their harness paths and run init again. Sync leaves stale `.gitignore`
entries from older versions in place rather than guessing which user-authored entries to delete.

To enable another harness, add it to `harnesses` in `loadout/config.toml` and run `loadout sync`.
Loadout derives every output path from its project preset:

| harness | generates |
| --- | --- |
| `claude` | `.claude/settings.json`, `.claude/mcp-permissions.json`, `.mcp.json`, `CLAUDE.md`, `.claude/skills/` |
| `codex` | `.codex/rules/permissions.rules`, `AGENTS.md`, `.agents/skills/` |
| `droid` | `.factory/settings.json`, `.factory/hooks.json`, `.factory/mcp.json`, `AGENTS.md`, `.factory/skills/` |
| `opencode` | `opencode.json`, `AGENTS.md`, `.opencode/skills/` |
| `pi` | `.pi/extensions/pi-permission-system/config.json`, `AGENTS.md`, `.pi/skills/` |

### Project instructions

`instructions` in `loadout/config.toml` names fragments in `loadout/instructions/`, in reading
order. They compose into `CLAUDE.md` and `AGENTS.md`. One order serves the whole repository because
four harnesses share `AGENTS.md`. With no instructions or template prose, neither document is
generated.

```toml
instructions = ["conventions", "testing"]
```

A template contributes instructions before project fragments, so project prose is read last.
Harness-owned multi-purpose JSON files preserve foreign top-level keys; Loadout replaces only the
keys belonging to its slices.

### Project skills

Put each authored skill document directly in `loadout/skills/`:

```text
loadout/skills/review.md
loadout/skills/review/reference.md
```

The matching directory is optional and holds supporting files. The directory form
`loadout/skills/review/SKILL.md` remains supported for existing catalogs.

A skill reaches every configured harness by default. Restrict one in `loadout/config.toml`:

```toml
[skills.review]
agents = ["claude", "droid"]
```

Use `review.local.md` and, when needed, `review.local/` for a private replacement of the complete
`review` skill. Project scaffolding ignores both forms. A local replacement is not an overlay:
its document and supporting files replace the public variant together.

Loadout renders `::: <harness>` sections and `:concept[…]` separately for each selected harness.
It writes Claude to `.claude/skills`, Codex to `.agents/skills`, Droid to `.factory/skills`,
OpenCode to `.opencode/skills`, and Pi to `.pi/skills`. Byte-identical Codex, OpenCode, and Pi
variants may share the `.agents/skills` convention.

**OpenCode needs `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` in your shell**, or it also scans
`.claude/skills/` and picks between the two copies of each skill at random. `loadout check` says
so when neither that variable nor `OPENCODE_DISABLE_CLAUDE_CODE` is set — advisory, never an exit
code. See [opencode.md](docs/reference/opencode.md#required-setup-opencode_disable_claude_code_skills).
When Codex and OpenCode need different variants of the same skill, also set
`OPENCODE_DISABLE_EXTERNAL_SKILLS=1` so OpenCode does not scan Codex's `.agents/skills` copy.

A template contributes `skills/` the same way it contributes `instructions.md`: a tier beneath the
project, so a skill the project defines under the same name replaces the template's.

### Project MCP servers

`loadout/mcp.toml` declares servers for enabled harnesses with a known destination:

```toml
# loadout/mcp.toml
[jina]
transport = "http"
url = "https://mcp.jina.ai/v1"
auth_env_var = "JINA_API_KEY"      # the NAME of an environment variable, never a token

[context7]
transport = "stdio"
command = "npx"
args = ["-y", "@upstash/context7-mcp"]
```

The same format is `<source>/mcp.toml` at global scope too. `transport` is `"http"` (needs `url`,
optionally `auth_env_var`) or `"stdio"` (needs `command`, optionally `args` and `env`) — refused
at parse time otherwise. `auth_env_var` names a variable; the value it holds never reaches a
rendered file.

At project scope this reaches Claude (`.mcp.json`), Droid (`.factory/mcp.json`), and OpenCode
(`opencode.json`'s `mcp` key).
Pi has no project destination — `.mcp.json` already serves it, since `pi-mcp-adapter` reads it
directly. Codex has none yet — whether it survives Codex's project-config filter is unverified.
A template contributes its own `mcp.toml` the same way it contributes `permissions.toml`,
beneath the project.

This is distinct from `[mcp]` in `permissions.toml`, which controls which of a server's *tools*
may be called, not which servers exist. Full detail:
[docs/reference/servers.md](docs/reference/servers.md).

### Templates

A template is shared configuration for a *kind* of project (`web`, `flutter`, `railway`) that a
repo opts into. It is a source, merged **beneath** both project tiers, so anything this repo
declares outranks it.

    loadout template add <name> --tracked    # declare it; it resolves from a source on every render
    loadout template add <name> --vendored   # copy it into loadout/templates/<name>/ and record its hash
    loadout template sync <name>             # update that vendored copy from its source
    loadout template list                    # every declared template, its mode, and how each resolves

```toml
# loadout/config.toml
harnesses = ["claude", "codex"]
instructions = ["conventions", "testing"]
templates = ["web", "railway"]

[template.web]              # written by `template add --vendored`; only `vendored` is accepted
vendored = "sha256:9f2a1c4e…"
```

Templates are referenced **by name, never by path** — a path in a committed file means nothing on
a colleague's machine. A name resolves to `loadout/templates/<name>/` if this repo vendored it,
then to the `templates/` directory of a source the machine's global manifest declares, and finally
to the packaged `frontend` or `backend` catalog when no declared source matches.
Two sources offering one name is an error, not a silent preference.

For shared parts, use manifests such as `loadout/templates/nextjs.toml`:

```toml
skills = ["review-typescript"]
instructions = ["typescript", "nextjs"]
mcp = ["github"]
permissions = ["node"]
```

Parts live under `templates/skills/`, `templates/instructions/`, `templates/mcp/` and
`templates/permissions/`, separate from active global folders. Another manifest can reference
the same parts. Vendoring keeps one shared copy; updating a shared part lists all affected
templates and asks for confirmation. Locally modified copies block the update.

Templates contribute instructions, skills, MCP servers and permissions through the same project
slices as directly authored source.

For directory templates, `template sync` is **refuse-and-diff**: it updates an unmodified copy, and on a copy you have
edited it prints the diff and exits 1 without changing anything. `loadout check` reports such a
copy but does not fail — a vendored template is source, not generated output
([0014](docs/decisions/0014-a-vendored-template-is-source-not-output.md)).

Full detail, including the content-hash definition: [docs/reference/templates.md](docs/reference/templates.md).

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean — nothing to do, or drift check found no differences |
| 1 | drift, refused safe update, interrupted migration, or recovery conflicts |
| 2 | usage error, unresolved init choices, or missing noninteractive approval |
| 3 | source or deployment error — missing/invalid input or receipt, unsafe destination or ownership collision |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
