# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

## Install

    just install

## Use

    loadout sync                  # regenerate generated files under the current repo
    loadout check                 # exit 1 if any generated file has drifted
    loadout explain <name>        # show which source a fragment resolves from, and which targets use it

`explain` takes a fragment name, optionally qualified as `source/name` to disambiguate when more
than one source declares a fragment with the same name.

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
order        = ["intro-claude", "web-fetching", "git-policy"]

[instructions.claude-autonomous]
output       = "claude/CLAUDE.autonomous.md"
destinations = ["~/.claude/CLAUDE.md"]
profile      = "autonomous"
order        = ["intro-claude", "web-fetching", "git-policy.autonomous"]
```

Each `[[source]]` is a named directory containing a `global/fragments/*.md` tree that fragments
are pulled from. Each `[instructions.<agent>]` table declares one generated file: `output` is
where it is written, relative to the repo root (it may not be absolute, empty, or escape the
root with `..`), `order` is the ordered list of fragment names composed into it, and
`destinations` documents where the rendered file is deployed outside the repo. At least one
source is required, and at least one `[instructions.<agent>]` or `[permissions.<name>]` target
must be declared; no two targets, of either kind, may share an `output` path.

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

`loadout` generates `.claude/settings.json` at project scope and leaves
`.claude/settings.local.json` alone — Claude Code writes to that file itself when you choose
"don't ask again", and Claude merges both at startup. A generator that owned it would delete
those grants on every sync.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean — nothing to do, or drift check found no differences |
| 1 | drift — generated files are out of date, run `loadout sync` |
| 2 | usage error — invalid or missing command-line arguments |
| 3 | source error — the manifest, a source, or a fragment is missing or invalid |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
