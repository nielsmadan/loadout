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

Outside a git repository or before a first commit, the committed baseline is unavailable.
Loadout reports when that limits protection. Review the named files when adopting an existing
setup.

## Check reports drift

Edit the source, then run the matching `loadout sync`. Check again afterward.

If the output was edited directly, follow the protected-edits steps above. Repeatedly
overwriting generated files by hand makes the next sync undo those changes.

## No global configuration

Run:

```sh
loadout init --global --source ~/agent-config
```

Then configure the created source as described in [global setup](guides/global.md).
A project-only setup does not require global configuration.

If a machine configuration already exists, inspect its `source` path first.
`init --global --force` is for deliberately replacing that machine selection.

## No targets declared after global initialisation

The generated global starter file contains commented examples. Replace it with the
[agent-block example](guides/global.md#choose-your-agents), then create the selected instruction
fragments. A source must declare at least one output target.

## A fragment cannot be found

Check the name against the selected source's directory:

- `instructions = ["workflow"]` needs `instructions/workflow.md`.
- `settings = "claude"` needs `settings/claude.json`.
- Source paths are resolved relative to the manifest directory.
- A source's `use` list may exclude the requested slice.

For global instructions, inspect resolution with:

```sh
loadout explain workflow --root ~/agent-config/loadout
```

If several sources offer the same name, qualify it as `personal/workflow`.

## A template cannot be found

Loadout first checks the project's vendored directory, then the template directories in sources
declared by the machine's global manifest.

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

The bundled `loadout skill uninstall` command has its own ownership-aware cleanup for the
skill it installed.

## An agent misses part of the configuration

Check the [support table](reference/harnesses.md). Some slices are global-only, and some depend
on a harness extension or adapter.

For OpenCode skills, check
[`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`](reference/harnesses.md#opencode-skills).
After installing new skills or changing agent startup configuration, restart the agent session.

## Unexpected internal error

Exit code `4` includes a traceback. Report it in the
[issue tracker](https://github.com/nielsmadan/loadout/issues) with the command, Loadout revision,
and a minimal configuration that reproduces it. Remove secrets and private source content from
the report.
