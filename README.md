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
source and at least one instructions target are required, and no two targets may share an
`output` path.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean — nothing to do, or drift check found no differences |
| 1 | drift — generated files are out of date, run `loadout sync` |
| 2 | usage error — invalid or missing command-line arguments |
| 3 | source error — the manifest, a source, or a fragment is missing or invalid |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
