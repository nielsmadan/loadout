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
    loadout integrate skill copy --harness claude  # a plain copy for each harness, outside any source

The command reads the machine config and active profile and vendors a normal `skills/loadout/`
source copy. An existing copy is selected using the same declared overrides as rendering;
`--source NAME` must identify that active source. When no copy exists and several declared
sources offer skills, choose the installation source with `--source NAME`. `--profile NAME`
overrides the active profile.

An ownership marker, `.loadout-bundle.toml` inside the source copy, records the installed content
hash; the skill renderer never copies it. Reinstalling refreshes an unchanged older copy. A source
copy you edited or an unowned skill with the same name is reported and left alone, and the command
exits 1. A generated output edited outside Loadout stops install's sync with exit 1, as it stops
any sync, and stops uninstall before it removes anything, with exit 3.
Uninstall removes only the owned source and the files rendered from it that sync would accept as
its own (the current render, what was committed, or what sync last wrote); unrelated files in the
destination directory survive.
When uninstalling an overriding copy, the command also removes its `loadout` override entry from
the manifest that declares it, preserving other entries, comments and file mode. The earlier
source's skill becomes active again and the command's sync deploys it. An inherited declaration
is updated in its declaring parent, affecting every profile that inherits those sources.
Each file is moved aside before it is removed and put back if a later step fails; one that cannot
be put back is left where the error names it.

Invoke the installed skill as `loadout` using the harness's skill syntax. Configuration defaults
to personal rules for the current project; use `--project` for committed repository configuration
or `--global` for machine-wide configuration. The skill edits Loadout sources, applies generic
requests to configured harnesses that support them, and runs the matching sync command. Restart an
already-running agent session after first installation so it refreshes its skill catalog.

A fragment name may be qualified as `source/name`. Loadout refuses an unqualified name that more
than one source declares, naming the candidates so you can pick one. Instruction fragments are
global scope only — they are not part of project scope (see below).

### Files modified outside loadout

Generated files look like ordinary files, so they get hand-edited — and the edit is then lost
on the next `sync`, silently. Before writing, `sync` compares each file against every output
loadout itself could have produced: rendered from the committed source and from the working
tree, under every declared profile. A file matching none of them was written by something
else, so `sync` names it with the kind `check` would report, changes nothing, and exits 1. It
prints the same diff `check` does alongside the name — the `-` lines are the ones that exist only
on disk, and for a key removed outside loadout the `+` lines are what sync would write back, so a permission a harness granted itself at
runtime is reported verbatim rather than silently discarded. When the terminal shortens that
diff, the whole of it goes to `$XDG_STATE_HOME/loadout/sync-abort.diff` (mode 0600), which the
next such abort replaces and the next sync that completes removes; when that directory cannot be
written, the abort says so by leaving the line out and still exits 1. A file holding a value the
credential check recognises is never diffed, there, in `check`, in `template sync` or in the init
preview: one line names it and counts the lines that differ, so read the file directly. Port the
edit into the source with the bundled `/loadout` skill, or pass `--force` to discard it.

Comparison is by parsed document for JSON targets, not by bytes, so a harness re-serialising
its own config in a different key order does not read as an edit. In a file loadout shares with
its harness, such as `~/.claude.json` or `~/.codex/config.toml`, only the keys loadout writes are
compared, each on its own: the harness may change the rest, while an edit to one of loadout's keys,
such as a server added to `mcpServers` with `claude mcp add`, stops sync and is reported by `check`
as `owned-key`, and deleting one, such as `codex mcp remove` of the last server, as
`owned-key-removed`. A key the source manages for the first time, beside keys sync has already
recorded writing there, is a source change: sync overwrites it and says so, as
`now managed: model in ~/.codex/config.toml (was "gpt-5")`. With no record of the file at all, on a
first sync or with the record deleted, a value one of loadout's keys already holds is overwritten
only when this render or the committed source accounts for it; anything else is refused as
`unknown`, while a key the file does not hold yet is simply added. A key listed under `$remove` is
never an edit, since the harness may write it back, not even when the file stops being rendered
and the key is stripped with the rest, and moving it from `$remove` to a value opts it
in like any newly managed key. When a slice stops rendering into a shared
file, each of its keys still there is judged the same way before sync strips it, so a server
added by hand stops the retirement as `owned-key` instead of going with it.

There is a third comparison beside those two renders: **what `sync` recorded writing there
last**, kept per destination under `$XDG_CONFIG_HOME/loadout/written/`. Without it, editing a
source twice before committing left an output rendered from a state that is neither baseline,
and `sync` refused a file it had written itself. For a file loadout writes whole the record only
ever widens acceptance: delete it and the guard behaves exactly as it did before, warning and all.
For a file it shares with a harness, deleting it makes the guard stricter, as described above. `sync` deletes the record of
any root that no longer exists; a root it cannot examine, such as one a sandbox hides, keeps its
record. `check` never changes machine state.

The guard does not need git, nor git on PATH; `check --staged`, which reads the index, exits 3
and names git when it cannot run it. Outside a repository, or before the first commit, the working
source and the record are the baseline: an unsynced source edit leaves the output matching what
was recorded, while a hand edit does not. On the very first `sync`, with neither a commit nor a
record, a file that does not match the working source is refused until you pass `--force`.

Two cases warn without anything being wrong. The first `sync` over config that predates
loadout — adoption is the one time loadout overwrites a file it has never written. And a
destination shared between machines through a symlinked config repo: one machine's record cannot
vouch for the other's write.

`check` asks the same question and says which answer applies, rather than sending you to `sync`
where `sync` is about to refuse.

### Outputs the source no longer produces

Remove a skill, a harness or the last MCP server from the source, and `sync` retires what it
wrote for them before: it deletes each file, takes a skill's folder with it, and drops the file's
`.gitignore` entry, printing `retired <path>`. In a file loadout shares with its harness, such as
`~/.codex/config.toml`, it removes only the keys it recorded writing and leaves the rest.

Only provably loadout's own, unchanged output is retired. The record above is the proof: a file
it does not list is never touched, however generated it looks. A file edited since loadout wrote
it is refused like any other hand edit, and `--force` retires it anyway. A skill folder holding a
file loadout never wrote keeps that file and the folder. `check` reports each pending retirement
as `orphaned`.

### Files loadout did not write, in folders it manages

Some folders are read whole: Codex reads every file in `~/.codex/rules/`, and every harness reads
every folder in its skills directory. A file there that loadout did not write takes effect beside
the source — Codex writing a new approval to `~/.codex/rules/default.rules` is the common case.
`check` reports it as `unmanaged` and exits 1; `sync` names it in a warning and carries on, since
it overwrites nothing there. Port it into the source with the bundled `/loadout` skill, then delete
it. A folder of files is reported once, as the folder. A link there is reported as
`unmanaged-link` with where it points, and never read: check the target, then delete the link. A
project folder reached through a link, such as `.claude/agents -> ../.git`, is reported the same
way and never scanned. A file another loadout
source wrote is not reported, nor is a dot entry such as `.DS_Store` or Codex's own `.system`
skills, nor what a harness writes there itself: Claude Code's `skills/synced`, its copy of your
claude.ai account's skills, and Droid's two mission droids in `~/.factory/droids/`. A source skill
or agent that would render onto one of those is refused with exit 3; rename it. Nor is an entry no
harness reading the folder would load, such as a `README.md` beside the skills in a Claude, Codex
or Droid skills folder, a folder holding no `SKILL.md`, or a Claude agent file without
frontmatter; init leaves those in place too. The rule per harness is in
[docs/reference/config.md](docs/reference/config.md#what-a-harness-loads-from-a-skills-or-agents-folder).

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
source    = "~/ac"                    # directory holding loadout.toml; absolute or ~/, must exist
profile   = "autonomous"              # optional; active profile, default "default"
harnesses = ["claude", "codex", "pi"] # optional; defaults for fresh project init
```

`source`, `profile`, and `harnesses` are the only accepted keys — anything else is an error, so a
typo fails loudly. A relative `source` is an error too: it would register whatever folder a command
runs in. An explicit `--harness` selection overrides the machine default. Existing
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

`init` finishes with the first render. It compares what loadout would write with what is on disk, writes it and says so when the two match, names a file that only gains loadout's banner or formatting as `Normalise:`, and shows the diff before applying when they differ otherwise. A `sync` straight after `init` changes nothing. It records what it wrote, so the first `sync` has a baseline.

Global init discovers existing configuration in live harness roots and the selected directory,
which defaults to cwd. Under a harness root it reads only the paths loadout manages and leaves
everything else alone unread, so runtime state, `.DS_Store` files and notes never stop it; the
[list](docs/reference/migration.md#what-discovery-reads) is per harness. It migrates into `<source>/loadout/`: a manifest with one block per harness,
the source slices it adopted beside it, and private sources where required. It registers that actual manifest directory and resolved
harness list. That list becomes the default for fresh project init. An existing Loadout manifest
is recognized as source and left intact; repeat init reports no migration while refreshing the
saved harness default.
A `~/.claude` symlinked into a dotfiles repo, or one made of per-file links, is adopted by
de-linking it: loadout's outputs become real files, everything else links back into the repo, and
the repo is left unchanged. Init names each link it replaced and which later additions to the repo
will not appear; see
[symlinked deployments](docs/reference/migration.md#symlinked-deployments).
A conflicting machine registration requires `--registration replace` or `--registration keep`.
`--yes` approves resolved operations.

Init always reads the live deployment, never a dotfiles copy of it in the selected directory, and
has no option to point it elsewhere; see [what init refuses](docs/reference/migration.md#what-init-refuses).
A skill or subagent whose copies differ between harnesses, or a project `CLAUDE.md` that disagrees
with `AGENTS.md`, does not stop init: that one item is left in place and reported for the `/loadout`
skill to merge, and everything else is adopted; see
[what init leaves out](docs/reference/migration.md#what-init-leaves-out).
A file holding a secret is adopted as it is: the preview and `--json` (`secrets`) name each source
file that looks like it holds one, so keep the source's remote private or move the secret into an
environment variable; see [what init keeps private](docs/reference/migration.md#what-init-keeps-private).
Unresolved previews exit 2 without mutation. Supported configurations get a read-only preview of
source and output writes, private exclusions and removals before approval. Before its first write,
init backs up every path it will touch to `$XDG_STATE_HOME/loadout/backups/` (default
`~/.local/state`): file contents and modes, symlinks, directories, and the Git index when it
untracks generated files. The backup lasts one run: init either converts or puts everything back.
A success deletes it, and a failure puts every path back from it, removes a repository or folder
init created, and exits 1. After a failure, or a preview init cannot complete, init recommends
doing the conversion with the bundled `/loadout` skill; at a terminal it offers to copy the skill
into each harness's user skills folder, and otherwise prints the command that does,
`loadout integrate skill copy --harness NAME`. If init is killed part way, the next init puts the
backup back first, asking at a terminal and refusing without one. Init commits nothing and stages nothing: in a project it only removes the generated
files it adds to `.gitignore` from the index, and it never creates a project's repository. A new
global source gets its own repository, and init suggests the commit. Choose a dedicated directory;
a new source repository at HOME or the filesystem root is refused.

A missing machine config is **not** an error — it means this machine has no global scope,
which is correct for someone who only uses project scope. `loadout sync --global` without one
fails and names the file to create. `loadout init project` notes its absence and carries on.

For `sync` and `check`, `--global` and `--root` are mutually exclusive; `--global` resolves the root from the machine
config. Every run picks its profile by one rule: `--profile` when given; otherwise the machine
config's `profile` when the root is the registered global source, whether named by `--global`,
by `--root`, by a git hook or by init; otherwise `default`. So a manual sync and a hook-run sync of
the global source render the same profile, and neither retires the other's outputs. A machine
config that cannot be read, a folder in its place included, stops a run at a global source rather than fall back to `default`,
which would retire the profile's outputs; a git hook there stops too, after git has finished,
and says what to run once the config is fixed. A project, hooks included, falls back to
`default`.

## `loadout.toml`

A repo's root `loadout.toml` — the global source's manifest — declares the sources fragments
come from and the instruction files to render from them. It renders only from this machine's
registered source, the directory the machine config names (`loadout init global <path>` registers
one). Anywhere else, a project included, a root `loadout.toml` is refused before anything is read
or written, with exit 3, so a cloned repository or a pull request cannot write under your home
directory ([0031](docs/decisions/0031-global-scope-renders-only-from-the-registered-source.md)). A top-level key it does
not read, whether a table or a value, is refused by name rather than ignored:

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

`[[source]]` is an ordered list. Permissions merge every contributing `permissions.toml`; a
source without one contributes no rules, and when no source has one the permission outputs render
exactly as they would from an empty file. MCP definitions use the last source for each server name. Named fragments require an unambiguous
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

`[codex]` and `[pi]` with no keys are complete declarations. **`permissions`, `mcp`, `skills`,
`agents` and `module-config` render without being asked for**, because none has an authoring
decision to make — for the last three the directory is the declaration, so dropping a tree in
renders it. Say `skills = false`, `agents = false` or `module-config = false` to opt out; an absent key means *automatic*. `instructions` must
be named — it needs an order, and alphabetical is wrong (see Profiles). `settings` must be named
because it is an input rather than an output.

`permissions = []` keeps the permissions target but selects none of the source's rules, so its
lists render empty while the settings around them still render: an autonomous profile's
`settings.json` with no `deny` for the classifier to defer to. `permissions = false` instead
stops managing the file, and the settings riding on it go too.

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

- The active profile is `--profile NAME` when given, else the machine config's `profile` when
  syncing or checking the registered global source, else the literal string `"default"`.
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
- A target always renders the source's rules. To render one holding none, with its settings
  still around it, declare the harness in an agent block with `permissions = []` (see
  [Agent blocks](#agent-blocks)); a `rules` key is an error.

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

`init` finishes with the first render. It compares what loadout would write with what is on disk, writes it and says so when the two match, names a file that only gains loadout's banner or formatting as `Normalise:`, and shows the diff before applying when they differ otherwise. A `sync` straight after `init` changes nothing.

To start from a small instruction seed, apply a bundled template after init with
`loadout template add frontend --vendored` (or `backend`), then `loadout sync`. Bundled templates
ship in the package and work offline. They add no dependencies, permission grants, models or
credentials.

Git hooks are not part of adopting configuration: install them with
`loadout integrate git-hooks install --root . [--regenerate]`, which reports exact integration
commands and preserves existing hooks. In a repository with linked worktrees it writes the hooks
directory they share once, from any of them, and the hooks run in each. `--regenerate` also runs guarded sync after
checkout and merge, and rewrites an out-of-date hook loadout wrote. A hook resolves its profile
each time it runs, the way `sync` does. A new clone needs that command explicitly. Manual validation is
`loadout check --staged`.
See [Git integration](docs/reference/git-integration.md) for dependencies, alternate indexes,
worktrees, profiles and post-hook failure behavior.

`init` adopts only configuration with a known slice destination. It extracts permissions,
instructions, settings, hooks, plugins, MCP servers, skills and global module configuration into
canonical sources, and each harness's subagents into one source agent per name. Harness-specific
command trees stay where their harness reads them;
Loadout neither moves nor ignores them.

A project source normally contains:

```text
loadout/config.toml          configured harnesses, instructions and templates
loadout/permissions.toml     shared permission rules, optional
loadout/instructions/        ordered instruction fragments
loadout/skills/              authored skills and supporting files
loadout/agents/              subagents, one Markdown file each
loadout/mcp.toml             MCP server definitions, when present
loadout/hooks.json           hook declarations, when supported
loadout/plugins.json         plugin declarations, when supported
loadout/settings/<harness>.json  the rest of a shared settings file, when present
```

A missing `permissions.toml` means the same as an empty one. The harnesses you declare are what
make loadout own their permission keys, not the file's existence, so without it `sync` writes each
key empty and clears whatever rules were there. Deleting the file clears your rules on the next
sync, just as emptying it does. Rules loadout did not write are refused first; see
[Files modified outside loadout](#files-modified-outside-loadout).

A file that holds nothing but permission rules is written only when it has one, in both scopes:
Codex's `permissions.rules` needs a shell rule Codex can express, and Claude's
`mcp-permissions.json` needs an `[mcp]` rule. Once its last rule is gone, the next sync removes a
file loadout wrote, unless it was edited since. Pi's global permission file is always written,
since even empty it makes `bash` ask; its project file is written only with a rule or a stated
`[shell] default`. Keys inside shared files such as `settings.json` and `opencode.json` are always
written, empty when there are no rules. Nor does sync create `~/.codex/config.toml` just to leave
it empty.

Project-scope sync writes only to real paths inside the project, never under `.git` or
`loadout/`, and edits `.gitignore` only when it is a regular file rather than a symlink. A symlink
committed to the repository can route outputs anywhere: `.claude -> ../../.claude` into a home
directory, or `.claude/skills/x -> ../../.git/hooks` into a hook the next checkout runs. So no link
may sit between the project root and an output, and the only output that may itself be a link is
`CLAUDE.md -> AGENTS.md` when the same sync writes both. Any other link from one output to another,
such as `.claude/settings.json -> ../AGENTS.md`, would have sync overwrite one with the other.
Sync refuses before writing anything and names each link and where it resolves. The same holds
for what it reads: inputs come from `loadout/` alone, and `loadout/` must be a real folder. A link
in it that leads anywhere else is refused rather than copied into the repository's outputs: a
skill's supporting file pointing at a key in `~/.ssh`, into `.git`, which holds credentials such
as a token in `.git/config`, or at a gitignored `.env` beside it. A template's own files are read
only as real files, never through a link.
Links that stay inside `loadout/` are followed; folders never read, such as a skill's `.venv` or
`node_modules`, are skipped, so a skill or template may not itself be named like one. A link the
file system cannot resolve, such as a pair that lengthens the path at every hop, is refused by name.
Both checks run before sync or check compares anything, a file sync wrote and no longer renders
included, so no diff of an outside file is shown.

Generated project files are ignored and removed from the final index while remaining available to
the harness. `.gitignore` lists exactly what sync wrote: with no `mcp.toml` there is no `.mcp.json`
entry, so one written by `claude mcp add` stays committable, and a skill is listed by its own folder
(`/.claude/skills/review/`), never the skills directory, so a skill installed there by hand stays
visible to git. Re-running init does not reinterpret an existing source; edit it and use `check` or
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

### Project settings

`.claude/settings.json`, `.factory/settings.json` and `opencode.json` are shared: loadout writes its
own keys into them (permissions, MCP servers, plugins), and `loadout/settings/claude.json`,
`droid.json` or `opencode.json` holds everything else, such as Claude's `env` or OpenCode's `theme`.
`sync` renders the file from both, so a clone gets the same file, and an edit to it outside loadout
is reported like any other. Without a settings file, the keys loadout does not write are kept from
the file on disk.

`init` fills these from the files it adopts. A file Git tracks is already shared with the team, so
its keys go into committed source. An untracked or ignored one may hold something personal, so its
keys go into a settings file that is itself gitignored and stays on this machine.

### Project instructions

`instructions` in `loadout/config.toml` names fragments in `loadout/instructions/`, in reading
order. One order serves the whole repository because four harnesses share `AGENTS.md`. With Claude
and any other harness, the content goes to `AGENTS.md` and `CLAUDE.md` holds only the banner and
`@AGENTS.md`: Claude reads `AGENTS.md` only when no `CLAUDE.md` is on the path, and its docs promise
the import never makes it read `AGENTS.md` twice. A Claude-only project writes the content to
`CLAUDE.md`; a project without Claude writes only `AGENTS.md`. A `CLAUDE.md` that is a symlink to
`AGENTS.md` stays one, and is the same document. With no instructions or template prose, neither
document is generated.

```toml
instructions = ["conventions", "testing"]
```

A template contributes instructions before project fragments, so project prose is read last.
Harness-owned multi-purpose JSON files preserve foreign top-level keys; Loadout replaces only the
keys belonging to its slices.

### Project skills

Give each skill its own directory in `loadout/skills/`, with supporting files beside it:

```text
loadout/skills/review/SKILL.md
loadout/skills/review/reference.md
```

That is the only skill form. A skill's folder name, like an agent's file name, may not hold a
control character such as a line break, a `/`, or be `.` or `..`, since it becomes a path and a
`.gitignore` line. A file directly in `loadout/skills/` is not a skill: `README.md`
documents the directory and is skipped silently, and any other loose file is skipped with a notice
from `sync` and `check`.

A skill reaches every configured harness by default. Restrict one in `loadout/config.toml`:

```toml
[skills.review]
agents = ["claude", "droid"]
```

Use `review.local/SKILL.md`, with its own supporting files, for a private replacement of the
complete `review` skill. A local replacement is not an overlay:
its document and supporting files replace the public variant together.

Loadout renders `::: <harness>` sections and `:concept[…]` separately for each selected harness.
Frontmatter that only Claude reads (`model`, `effort`, `context` and `agent`) goes into Claude's
copy only; a harness block such as `droid:` with `model: …` under it sets a key for that harness.
A skill with `agent: reviewer` hands its work to the `reviewer` agent from `loadout/agents/`:
Claude's copy also gets `context: fork`, and every other harness's copy opens with *"Delegate this
task to the `reviewer` subagent."* Naming an agent the source does not define is an error.
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

### Agents

A subagent is one Markdown file, `agents/<name>.md` in a global source (whose `use` must include
`agents` if it lists categories) or `loadout/agents/<name>.md` in a project, and it reaches every
configured harness: Claude (`.claude/agents/`), Codex (`.codex/agents/<name>.toml`), Droid
(`.factory/droids/`), OpenCode (`.opencode/agents/`) and Pi (`.pi/agents/`, read by the
`pi-subagents` package, which Pi needs installed). Global scope writes the same folders under
each harness's home.

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
A block keyed by a harness holds what only that harness reads. Removing or renaming the file
retires the old agent everywhere. Details: [models.md](docs/reference/models.md#what-loadout-does).

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
| 1 | drift, refused safe update, or an init that failed and put everything back |
| 2 | usage error, an init plan that cannot complete, missing noninteractive approval, or an init killed part way, found by the next one |
| 3 | source error — missing or invalid input, unsafe destination, or ownership collision |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
