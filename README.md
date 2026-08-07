# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

## Install

    just install

## Use

    loadout sync                  # regenerate generated files under the current repo
    loadout sync --profile NAME   # regenerate under a specific active profile (see Profiles below)
    loadout check                 # exit 1 if any generated file has drifted
    loadout check --profile NAME  # check drift under a specific active profile
    loadout explain <name>        # show which source a fragment resolves from, and which targets use it

`explain` takes a fragment name, optionally qualified as `source/name` to disambiguate when more
than one source declares a fragment with the same name. `explain` is global scope only —
instruction fragments are not part of project scope (see below).

## `loadout.toml`

A repo's root `loadout.toml` declares the sources fragments come from and the instruction files
to render from them:

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

Each `[[source]]` is a named directory containing a `global/fragments/*.md` tree that fragments
are pulled from. Each `[instructions.<agent>]` table declares one generated file: `output` is
where it is written, relative to the repo root (it may not be absolute, empty, or escape the
root with `..`), `order` is the ordered list of fragment names composed into it, and
`destinations` is where else the same rendered bytes are written — real paths on the machine,
such as `~/.claude/CLAUDE.md`, with `~` expanded to the user's home directory. `sync` and `check`
write and diff every destination exactly like the in-repo `output`, so both stay byte-identical.
`[permissions.<name>]` targets accept `destinations` the same way. At least one source is
required, and at least one `[instructions.<agent>]` or `[permissions.<name>]` target must be
declared; no two targets, of either kind, may share an `output` path, and — among the targets
selected for the active profile — no two may share a `destination` either; that raises a
`LoadoutError` naming both.

### Profiles

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
`permissions/permissions.toml` rule file. Each `[permissions.<name>]` table renders that rule
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
  `antigravity`, `opencode`.
- `base` — optional. A JSON file, relative to the repo root, that the renderer starts from —
  hand-maintained keys in it (model, hooks, `defaultMode`, and so on) are carried through into
  the output untouched. A `base` must be an existing input file; it may never point at a path
  that is itself a generated `output` (that would reintroduce reading a renderer's own prior
  output as its template, which this design deliberately avoids).
- `preserve` — optional list of top-level keys to copy forward from the target's *existing*
  output file, for keys owned by some other generator (for example, `mcp` in `opencode.json`,
  owned by the MCP sync). A key named in `preserve` must not also be one the renderer generates.
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
| `claude` | `.claude/settings.json`, `.aiconf/mcp-permissions.json` |
| `codex` | `.codex/rules/aiconf.rules` |
| `opencode` | `opencode.json` |
| `pi` | `.pi/extensions/pi-permission-system/config.json` |
| `antigravity` | nothing — `agy` has a project permission scope in its TUI, but the system loadout ported never wrote one and its storage path is unknown; the name is still accepted so a project's harness list can name every harness in use |

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
