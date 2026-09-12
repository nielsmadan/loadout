---
title: CLI reference
description: Loadout commands, scope flags, profile selection, template operations, and exit codes.
---

# CLI reference

Run `loadout --help` or `loadout COMMAND --help` for the installed version's help.
Commands default to the current directory. Run them from the source or repository root, or
supply `--root PATH`.

## Render and check

```sh
loadout sync
loadout check
loadout sync --root /path/to/repository
loadout check --root /path/to/repository
loadout sync --global
loadout check --global
loadout sync --global --profile focused
loadout check --global --profile focused
```

| Option | Commands | Meaning |
| --- | --- | --- |
| `--root PATH` | `sync`, `check` | Directory containing the source/project configuration |
| `--global` | `sync`, `check` | Resolve the source and default profile from this machine's configuration |
| `--profile NAME` | `sync`, `check` | Select a global manifest profile |
| `--force` | `sync` | Overwrite generated files protected as modified outside Loadout |

`--root` and `--global` are mutually exclusive. The explicit profile wins over the machine
configuration's selection; otherwise the default is `default`.

`sync` writes generated outputs. `check` compares them with the current source and reports
drift without rewriting the generated files. A root containing both global and project
configuration renders both scopes.

Use `--force` only after reviewing the reported output edits and deciding to discard them.
It does not resolve invalid sources or merge a modified vendored template.

## Initialise

```sh
loadout init --harness claude --harness codex
loadout init --harness opencode --root /path/to/repository
loadout init --global --source ~/agent-config
```

| Option | Meaning |
| --- | --- |
| `--harness NAME` | Enable a project harness; repeat for several |
| `--root PATH` | Project directory to initialise |
| `--global` | Initialise or adopt a global source and write the machine configuration |
| `--source PATH` | Existing global source, or directory in which to scaffold a new one |
| `--force` | With `--global`, overwrite an existing machine configuration |

Project initialisation requires at least one `--harness`. Global initialisation cannot be
combined with `--harness`. In a non-interactive shell, supply `--source` for global setup.

Re-running project initialisation with the same harness set preserves its configuration and
extends the generated paths in `.gitignore`.

## Add a project harness

```sh
loadout harness add opencode
loadout harness add pi --root /path/to/repository
```

The accepted names are `claude`, `codex`, `opencode`, and `pi`.
Run `loadout sync` after changing the enabled set.

## Inspect a fragment

```sh
loadout explain workflow --root ~/agent-config/loadout
loadout explain personal/workflow --root ~/agent-config/loadout
```

`explain` reports global instruction-fragment resolution and consumers.
It accepts `--root`, not `--global`. A qualified name disambiguates sources.

## Templates

```sh
loadout template add python
loadout template vendor python
loadout template sync python
loadout template list
```

All four accept `--root PATH`.

| Command | Result |
| --- | --- |
| `add NAME` | Declare the template for the project |
| `vendor NAME` | Copy its source into the project and record its content hash |
| `sync NAME` | Update an unchanged vendored copy from the available upstream source |
| `list` | Show declared templates and where each resolves |

Run the ordinary `loadout sync` to render the resulting project configuration.
See [templates](../guides/templates.md) for the complete lifecycle.

## Bundled skill

```sh
loadout skill install
loadout skill install --yes
loadout skill status
loadout skill uninstall
```

All three commands accept `--profile NAME` and `--source NAME`. Here `--source` is a
declared source name, rather than the directory accepted by `init --global`.

`install` and `uninstall` accept `--yes` to apply the displayed change without a prompt.
They operate on the global source and synchronise the selected profile.

Installation and removal protect edited skill copies. An unchanged installed copy can be
upgraded or removed; a conflicting local edit is reported for review.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, including a clean check |
| `1` | Generated-file drift or a refused safe update |
| `2` | Invalid command-line usage |
| `3` | Source, configuration, or resolution error |
| `4` | Unexpected internal error, with a traceback |

A modified vendored template is a source change. `check` can report it without failing solely
for that reason. A refused `template sync` exits `1`.
