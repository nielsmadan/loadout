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
loadout init project --harness claude --harness codex
loadout init project --harness opencode --root /path/to/repository
loadout init global --source ~/agent-config
```

| Option | Commands | Meaning |
| --- | --- | --- |
| `--harness NAME` | `init project`, `init global` | Select a harness; repeat for several |
| `--root PATH` | `init project` | Project directory to initialise |
| `--source PATH` | `init global` | Existing global source, or directory in which to scaffold a new one |
| `--force` | `init global` | With a conflicting registration, alias for `--registration replace` |

Scope is a subcommand: `init project` for a repository, `init global` for machine-wide
configuration. `--source` on `init global` defaults to the current directory, so a bare
`init global` adopts the cwd. `--root` exists only on `init project`; `--registration keep|replace` and `--force` only on `init global`. An
interrupted migration is resumed or recovered with `loadout init resume <journal> --yes` or
`loadout init recover <journal> --yes`.

Fresh project initialisation uses explicit `--harness` options, then the machine configuration's
`harnesses` default, then discovered project configuration. If none establishes membership, it
requires a selection. Global initialisation accepts `--harness` and automatically records its
resolved harness list as the project default. In a non-interactive shell, supply `--source` for
global setup.

Re-running project initialisation with the same harness set preserves its configuration and
extends the generated paths in `.gitignore`.

## Add a project harness

Add the name to `harnesses` in `loadout/config.toml`, then run `loadout sync`:

```toml
harnesses = ["claude", "opencode", "pi"]
```

The accepted names are `claude`, `codex`, `droid`, `opencode`, and `pi`. `sync` writes the new
harness's outputs and extends `.gitignore` to cover every generated path; entries are appended
only, so removing a harness from the list leaves its entry in place.

## Templates

```sh
loadout template add python --tracked
loadout template add python --vendored
loadout template sync python
loadout template list
```

All four accept `--root PATH`.

| Command | Result |
| --- | --- |
| `add NAME --tracked` | Declare the template; it resolves from a source on every render |
| `add NAME --vendored` | Copy the source into the project and record its content hash |
| `add` (bare) | List every resolvable template and prompt for a name and a mode; refused without a terminal |
| `sync NAME` | Update an unchanged vendored copy from the available upstream source |
| `list` | Show declared templates, each one's mode, and where it resolves |

Adding a template that is already declared changes its mode in either direction.

Run the ordinary `loadout sync` to render the resulting project configuration.
See [templates](../guides/templates.md) for the complete lifecycle.

## Integrations

Commands that wire loadout into another system so it runs automatically.

### Bundled skill

```sh
loadout integrate skill install
loadout integrate skill install --yes
loadout integrate skill status
loadout integrate skill uninstall
```

All three commands accept `--profile NAME` and `--source NAME`. Here `--source` is a
declared source name, rather than the directory accepted by `init global`.

`install` and `uninstall` accept `--yes` to apply the displayed change without a prompt.
They operate on the global source and synchronise the selected profile.

Installation and removal protect edited skill copies. An unchanged installed copy can be
upgraded or removed; a conflicting local edit is reported for review.

### Git hooks

```sh
loadout integrate git-hooks install --root .
loadout integrate git-hooks install --root . --regenerate
loadout integrate git-hooks status --root .
loadout integrate git-hooks uninstall --root . --yes
```

All three accept `--root` and `--profile NAME`. `install` also accepts `--dry-run`, and
`--regenerate` to add the post-checkout and post-merge hooks alongside pre-commit.

`uninstall` removes only hooks loadout wrote, identified by the marker on the second line of
the generated script rather than by the command they invoke. A hook without that marker is
never touched, whoever wrote it. A hook that loadout wrote for a different source in the same
repository is left alone too. `--yes` skips the confirmation prompt.

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
