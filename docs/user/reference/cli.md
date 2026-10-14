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
| `--json` | `check` | Emit `status`, `exit_code`, each out-of-sync `path` and any notes as JSON; not with `--staged` |

`--root` and `--global` are mutually exclusive. The explicit profile wins over the machine
configuration's selection; otherwise the default is `default`.

`sync` writes generated outputs. It rewrites only files whose bytes change, so an unchanged file
keeps its modification time, prints `wrote <path>` for each one it changed, and ends with
`N written, M unchanged`, or `Up to date.` when nothing changed. It also retires what it wrote
before and no longer renders, printing `retired <path>` and adding `, N retired` to the summary:
an unchanged file is deleted with its `.gitignore` entry, and a shared file loses only the keys
loadout recorded writing. A retired file edited since sync wrote it is refused like any other
hand edit. `check` compares them with the current source and reports
drift without rewriting the generated files, so `check` is the preview of `sync`; there is no
`sync --dry-run`. Each drifted file carries a kind, judged by the same comparison `sync` uses to
refuse an overwrite: `source-changed` (the source moved on; run `sync`), `hand-edited` (it differs
from what sync last wrote; port the edit into the source with `/loadout`, or `sync --force` to
discard it), `unknown` (no record and no committed baseline to judge by; review the diff, then port
or discard the same way), `missing`,
`orphaned` (sync wrote it, the source no longer produces it, and the next `sync` retires it), or
`unmanaged` (loadout did not write it, but it sits in a folder the harness reads whole, such as a
skills folder or Codex's rules folder; port it with `/loadout`, then delete it — `sync` warns
about it and carries on).
`check --json` reports the same kind per path and exits with the same code as `check`. A root containing both
global and project configuration renders both scopes.

JSON output (`init --json`, `check --json`) is not covered by a stability promise in 1.0.

Use `--force` only after reviewing the reported output edits and deciding to discard them.
It does not resolve invalid sources or merge a modified vendored template.

## Initialise

```sh
loadout init project --harness claude --harness codex
loadout init project --harness opencode --root /path/to/repository
loadout init global ~/agent-config
```

| Option | Commands | Meaning |
| --- | --- | --- |
| `--harness NAME` | `init project`, `init global` | Select a harness; repeat for several |
| `--root PATH` | `init project` | Project directory to initialise |
| `DIR` (positional) | `init global` | Existing global source, or directory in which to scaffold a new one |

Scope is a subcommand: `init project` for a repository, `init global` for machine-wide
configuration. `init global [DIR]` takes the directory as an optional argument, like
`git init [dir]`, and defaults to the current directory, so a bare `init global` adopts the cwd. `--root` exists only on `init project`; `--registration keep|replace` only on `init global`.
`loadout init restore [--yes] [--json]` puts back everything the latest init changed, from the
backup it took first; a path changed since that init is kept and reported, and the exit code is 1.
An init that was killed part way is offered for restore by the next `init`, which otherwise exits
2 with `"status": "interrupted"` under `--json`.

Fresh project initialisation uses explicit `--harness` options, then the machine configuration's
`harnesses` default, then discovered project configuration. If none establishes membership, it
requires a selection. Global initialisation accepts `--harness` and automatically records its
resolved harness list as the project default.

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

All four accept `--root PATH` and work on a project. Outside one they exit 3: templates are
applied to projects and authored as `templates/<name>/` in your global source.

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

All three commands accept `--profile NAME` and `--source NAME`. `--source` names a declared
`[[source]]` from the manifest; it is not a directory.

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

### Shell completions

```sh
loadout integrate completions install
loadout integrate completions status
loadout integrate completions uninstall --shell zsh
```

Completes subcommands and fixed-choice options for bash, zsh and fish. `--shell` defaults to the
shell running loadout, then to `$SHELL`; when neither is one of the three, the command exits 2 and
asks for `--shell`. The script is generated once, at install, so a TAB costs no Python start-up.

| Shell | Installed at | Loaded by |
| --- | --- | --- |
| bash | `${BASH_COMPLETION_USER_DIR:-${XDG_DATA_HOME:-~/.local/share}/bash-completion}/completions/loadout` | bash-completion 2, on demand; otherwise `source` it from `~/.bashrc` |
| zsh | `${XDG_DATA_HOME:-~/.local/share}/zsh/site-functions/_loadout` | `compinit`, once that directory is on `fpath` |
| fish | `${XDG_CONFIG_HOME:-~/.config}/fish/completions/loadout.fish` | fish, automatically |

Loadout never edits a shell startup file; `install` prints the one line to add where one is
needed. `status` reports whether the script is installed and current, since an upgrade can add
commands. `uninstall` removes only a script carrying loadout's marker, and installing over a file
without it is refused.

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
