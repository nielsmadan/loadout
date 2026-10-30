---
title: Troubleshooting
description: Resolve drift, protected edits, missing sources, template update conflicts, and agent setup notices.
---

# Troubleshooting

Start with the matching check command:

```sh
loadout check
loadout check --global
```

Run the first from the project root. Use the second for the machine-wide source.

## Files modified outside Loadout

Sync protects files whose content it cannot recognise as a Loadout-generated version.
It reports a diff and refuses to overwrite the edits.

1. Read the reported diff.
2. Move changes you want to retain into the appropriate Loadout source.
3. Run `loadout sync` again.
4. If the remaining output-only edits should be discarded, rerun sync with `--force`.

A harness granting a permission interactively can change a generated file. Record that grant
in `permissions.toml` or `permissions.local.toml` so it survives future syncs.

Loadout compares current output with the working source, committed source when available, and
what it last recorded writing. A destination shared between machines can still trigger a warning,
because one machine's record does not identify another machine's writes.

Outside a git repository or before a first commit, the working source and the record are the
baseline, so protection does not depend on git. On a first sync with no record yet, any file that
does not match the working source is refused. Review the named files when adopting an existing
setup, then pass `--force` once.

## Check reports drift

`check` labels each drifted file. `source-changed` and `missing` mean the source is ahead: run
the matching `loadout sync`, then check again.

`hand-edited` means the file was edited directly; follow the protected-edits steps above. This
includes a file the source no longer produces: sync refuses to retire it until you move the edit
into the source, delete the file yourself, or pass `--force`.
`orphaned` means loadout wrote the file and the source no longer produces it; the next `sync`
retires it.
`owned-key` means a key loadout writes in a file it shares with the harness, such as a server in
`~/.claude.json` added with `claude mcp add`, was edited there; port it into the source with
`/loadout`, or pass `--force` to discard it. `owned-key-removed` means such a key was removed
there: remove it from the source too, or pass `--force` to write it back.
`unknown` means loadout has neither a record of what it wrote nor a committed baseline, so it cannot
tell a source change from a hand edit: read the diff before syncing.
`unmanaged` is a file loadout did not write in a folder the harness reads whole, such as a skills
folder; port it and delete it. `unmanaged-link` is a link there; check where it points before
deleting it. `unparsable` is a shared file, global or a project's `.claude/settings.json`,
`.factory/settings.json` or `opencode.json`, that no longer parses, or is not even UTF-8: plain
`sync` refuses it with exit 3 and names it. Fix or delete it, then sync, or run `sync --force` to
replace it; one the source no longer renders has nothing to replace it with, so only fixing or
deleting it helps.

## Sync or check refuses a global setup

A folder holding a `loadout.toml` renders only if it is this machine's registered global source.
Anywhere else, `sync` and `check` exit 3 before reading it, and say why: this machine has no
registered source, so run `loadout init global <folder>` to register this one; or another source
is registered, so add `--registration replace` to register this one instead. A machine config
that does not load is reported as its own error: fix the key it names, or, when it cannot be
read, the file's or its folder's permissions.

## A skill named README, or a skill that stopped updating

A skill is `skills/<name>/SKILL.md`. Older versions also read every `skills/*.md` file as a skill,
so a `skills/README.md` rendered as a skill named `README`. Loadout no longer renders those files,
and the next `sync` retires the folders it wrote for them, such as `~/.claude/skills/README/`,
unless you edited them. A skill you kept as `skills/<name>.md` must move to
`skills/<name>/SKILL.md`; `sync` and `check` name any loose file they skip.

## No global configuration

Run:

```sh
loadout init global ~/agent-config
```

Then configure the created source as described in [global setup](guides/global.md).
A project-only setup does not require global configuration.

If a machine configuration already exists, inspect its `source` path first.
`loadout init global --registration replace` is for deliberately replacing that machine selection.

## No targets declared

A manifest must declare at least one `[<agent>]` block. `init global` writes one for each harness
it adopted; if they were removed, add one back as in the
[agent-block example](guides/global.md#choose-your-agents).

## A fragment cannot be found

Check the name against the selected source's directory:

- `instructions = ["workflow"]` needs `instructions/workflow.md`.
- `settings = "claude"` needs `settings/claude.json`.
- Source paths are resolved relative to the manifest directory.
- A source's `use` list may exclude the requested slice.

If several sources offer the same name, loadout does not pick one: it refuses the unqualified
name and lists the candidates it found. Qualify it as `personal/workflow` to choose. A command for
inspecting resolution directly is not available in 1.0.

## A template cannot be found

Loadout first checks the project's vendored directory, then the template directories in sources
declared by the machine's global manifest, then the bundled starters (`frontend` and `backend`),
which ship with loadout and also resolve on a machine with no global configuration.

Run `loadout template list` and inspect those sources. Clone or update an external source using
your normal git workflow. Loadout does not fetch it automatically.

If two sources offer the same name, use a qualified name such as `company/python`.

## A vendored template will not update

`loadout template sync NAME` refuses to overwrite a copy that has changed since it was
vendored, or whose original hash cannot be verified. It prints a diff for you to reconcile.

Keep project-specific changes in project fragments rather than editing the shared template
copy where possible. See [template updates](guides/templates.md#update-a-vendored-template).

A modified vendored source is not itself generated-file drift. `check` reports it without
failing solely for that reason.

## A removed skill or file is still present

Removing a source or disabling an output does not currently remove every old destination.
Inspect the obsolete output and remove it manually once you know nothing else owns it.

The bundled `loadout integrate skill uninstall` command has its own ownership-aware cleanup for
the skill it installed.

## An agent misses part of the configuration

Check the [support table](reference/harnesses.md). Some slices are global-only, and some depend
on a harness extension or adapter.

For OpenCode skills, check
[`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`](reference/harnesses.md#opencode-skills).
After installing new skills or changing agent startup configuration, restart the agent session.

## Init could not convert a setup

When init fails part way it puts every file it changed back and exits 1. Your setup is as it was
before, and init kept no backup. When it cannot plan the conversion, it exits 2 without changing
anything. Either way it recommends the bundled `/loadout` skill, which can do the conversion with
you. At a terminal it offers to copy the skill into your current setup. Without one, it prints the
command instead, for example `loadout integrate skill copy --harness claude`. Then start the skill
in that harness and ask it to set up loadout. A different `loadout` skill already in one of those
folders is refused rather than overwritten; remove it, or use the copy that is there.

## An earlier init was stopped part way

An init killed before it finished leaves its changes in place and a backup in
`$XDG_STATE_HOME/loadout/backups/`, and every later init stops there until it is put back. Run
`loadout init project` or `loadout init global` at a terminal and answer yes: it puts back every
path that init changed, losing any edit made to those paths since, and then exits. Run init again
to adopt. Without a terminal, init exits 2 and says so. A backup from another version of loadout
cannot be put back; init names its directory, which you delete.

## Unexpected internal error

Exit code `4` includes a traceback. Report it in the
[issue tracker](https://github.com/nielsmadan/loadout/issues) with the command, Loadout revision,
and a minimal configuration that reproduces it. Remove secrets and private source content from
the report.
