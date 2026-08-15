# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

## Install

    just install

## Use

    loadout sync                  # regenerate generated files under the current repo
    loadout sync --global         # regenerate this machine's global configuration
    loadout sync --profile NAME   # regenerate under a specific active profile (see Profiles below)
    loadout sync --force          # regenerate even over files modified outside loadout
    loadout check                 # exit 1 if any generated file has drifted
    loadout check --global        # check drift in this machine's global configuration
    loadout check --profile NAME  # check drift under a specific active profile
    loadout explain <name>        # show which source a fragment resolves from, and which targets use it

`explain` takes a fragment name, optionally qualified as `source/name` to disambiguate when more
than one source declares a fragment with the same name. `explain` is global scope only —
instruction fragments are not part of project scope (see below).

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

Three cases warn without anything being wrong. The first `sync` over config that predates
loadout — adoption is the one time loadout overwrites a file it has never written. Reverting
an uncommitted source edit that was already synced, which leaves outputs from a source state
that exists nowhere. And a file whose committed baseline is unavailable — outside a git repo,
or before the first commit — where an unsynced edit cannot be told from a hand edit, so the
check is skipped entirely and says so.

## Global scope

Global scope is the configuration that applies to every project on this machine. A **machine
config** says where its source lives:

```toml
# $XDG_CONFIG_HOME/loadout/config.toml, or ~/.config/loadout/config.toml
source  = "~/ac"            # directory holding loadout.toml; ~ expanded; must exist
profile = "autonomous"      # optional; the active profile (default: "default")
```

`source` and `profile` are the only accepted keys — anything else is an error, so a typo fails
loudly. The file is machine state: never version-controlled, never generated, and the only
place loadout *stores* state that is not part of a source (see
[0010](docs/decisions/0010-a-machine-config-locates-the-global-source.md) and
[0008](docs/decisions/0008-generated-files-carry-no-machine-state.md)). It is not the only
machine state loadout *reads*: a destination template resolves environment variables at render
time, per [0011](docs/decisions/0011-a-destination-follows-a-relocated-harness.md).

    loadout init --global --source ~/ac    # scaffold a global source and write the machine config
    loadout init --global --force          # reinitialise, overwriting an existing machine config

`init --global` creates `<source>/loadout/` holding `loadout.toml`, `permissions.toml` and
`instructions/`, then writes the machine config pointing at it. It refuses to overwrite an
existing machine config without `--force`. It is non-interactive so it works in a script:
`--source` is required unless stdin is a TTY, in which case it prompts with a default.

A missing machine config is **not** an error — it means this machine has no global scope,
which is correct for someone who only uses project scope. `loadout sync --global` without one
fails and names the file to create. `loadout init` at project scope notes its absence and
carries on.

`--global` and `--root` are mutually exclusive; `--global` resolves the root from the machine
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

`[[source]]` is a list, and its **order is the tier order, lowest priority first**. A source that
provides a `permissions.toml` contributes a tier; every contributing source is merged.

```toml
[[source]]
name = "company"          # a repo you cloned — loadout never fetches it
path = "~/src/acme-loadout"

[[source]]
name = "me"
path = "."
```

Merging is union with **deny wins**: a deny in any source beats an allow in any other, whichever
order they appear in. Order still matters for *emission* — OpenCode and Pi resolve last-match-wins
— so entries from earlier sources are emitted first.

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

`[permissions.<name>]` targets accept `destinations` the same way. At least one source is
required, and at least one `[instructions.<agent>]` or `[permissions.<name>]` target must be
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

`[codex]` and `[pi]` with no keys are complete declarations. **`permissions` and `mcp` render
without being asked for**, because neither has an authoring decision to make. `instructions` must
be named — it needs an order, and alphabetical is wrong (see Profiles). `settings` must be named
because it is an input rather than an output.

`hooks` and `plugins` must be named too, and for the same reason as `instructions`: which hooks
run and which plugins are on are authoring decisions, so an absent key means *loadout does not
manage this*, not *none*. Both take fragment names, resolved as `<source>/hooks/<name>.json` and
`<source>/plugins/<name>.json` and deep-merged in order — maps merge key by key, lists
concatenate, and `null` removes, which is how a profile switches one plugin off with a one-key
overlay instead of restating a list. See [docs/reference/plugins.md](docs/reference/plugins.md)
for what a plugin reference holds and what each harness makes of it.

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

`extends` names the profile to start from, and the file states only what differs. Blocks merge
**per key**: a block naming one key inherits the rest, so a profile can add a `substitute`
without restating the instruction order it is substituting into. Absent means inherit; an
explicit `[]` means empty, the convention `permissions = []` already set. A cycle in `extends`
is an error naming the cycle.

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
- `render` — the renderer name. One of: `claude`, `claude-mcp`, `codex`, `codex-mcp`, `pi`,
  `opencode`.
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

Per-repo permissions, layered on top of the global manifest above. A repo opts in once:

    loadout init --harness claude --harness opencode   # repeatable, one per harness
    loadout harness add pi                             # enable one more, later

`init` scaffolds `loadout/`:

```text
loadout/config.toml               enabled harnesses — the only key it accepts
loadout/permissions.toml          committed, shared with everyone working in this repo
loadout/permissions.local.toml    personal, gitignored
```

Both commands also add every output the enabled harnesses generate to `.gitignore`. Generated
project files are **gitignored and never committed** — the merged output mixes in
`permissions.local.toml`'s personal rules, so two people would conflict on every regeneration.

**Which file do I edit?** `loadout/permissions.toml` (shared) or `loadout/permissions.local.toml`
(personal) — never a generated output. Same rule as global scope: a generated file carries no
marker that it's generated, and anything written directly into one is discarded the next time
it's regenerated.

Known harnesses and what each one generates:

| harness | generates |
| --- | --- |
| `claude` | `.claude/settings.json`, `.claude/mcp-permissions.json` |
| `codex` | `.codex/rules/permissions.rules` |
| `opencode` | `opencode.json` |
| `pi` | `.pi/extensions/pi-permission-system/config.json` |

`opencode.json` and `.claude/settings.json` are a harness's own multi-purpose config file, so
loadout preserves any foreign top-level key already there (`$schema`, for example) instead of
overwriting the whole document. The other three outputs are loadout-only and always render from
a blank document.

`.claude/settings.json` is loadout's Claude output at project scope; Claude Code writes to
`.claude/settings.local.json` itself when you choose "don't ask again", and merges both at
startup. A generator that owned `.local.json` would delete those grants on every sync.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean — nothing to do, or drift check found no differences |
| 1 | drift — generated files are out of date, run `loadout sync` |
| 2 | usage error — invalid or missing command-line arguments |
| 3 | source error — the manifest, a source, or a fragment is missing or invalid |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
