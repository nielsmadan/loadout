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
| `--root PATH` | `sync`, `check` | Directory containing the source/project configuration; a `loadout.toml` there renders only when the directory is this machine's registered global source |
| `--global` | `sync`, `check` | Resolve the source and default profile from this machine's configuration |
| `--profile NAME` | `sync`, `check` | Select a global manifest profile; without it, the registered global source uses the machine's profile and any other root uses `default` |
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
from what sync last wrote, including bytes that are not UTF-8, which are shown with `�` in
their place; port the edit into the source with `/loadout`, or `sync --force` to discard it), `owned-key` (a key loadout writes in a file it shares with the harness, such as a
server in `~/.claude.json`, was edited there; port it with `/loadout`, or `sync --force` to discard
it), `owned-key-removed` (such a key was removed there; remove it from the source too, or
`sync --force` to write it back), `unknown` (no record and no committed baseline to judge by;
review the diff, then port or discard the same way), `missing`,
`orphaned` (sync wrote it, the source no longer produces it, and the next `sync` retires it), or
`unmanaged` (loadout did not write it, but it sits in a folder the harness reads whole, such as a
skills folder or Codex's rules folder; port it with `/loadout`, then delete it — `sync` warns
about it and carries on), `unmanaged-link` (a link loadout did not create in such a folder; it has
nothing to port, so check where it points before deleting it), or `unparsable` (a file loadout shares with a harness, such as
`~/.claude.json`, or in a project `.claude/settings.json`, `.factory/settings.json` or `opencode.json`, no longer parses, including one that is not even UTF-8, or parses but cannot hold loadout's keys, such as a JSON
array or a TOML scalar where a table goes: fix or delete it and sync, or `sync --force` replaces it
outright; plain `sync` refuses with exit 3 and names the file. One the source no longer renders
has nothing to replace it, so `sync` refuses it with or without `--force` until it is fixed or
deleted).
`check --json` reports the same kind per path and exits with the same code as `check`. The registered global
source may also hold a project configuration, and then renders both scopes. Any other root holding
a `loadout.toml` is refused with exit 3 before anything is read or written, its project
configuration included. The refusal names its cause: this machine has no registered source (run
`loadout init global <that folder>`), or another one is registered (add `--registration replace`).
A machine config that does not load is reported as its own error instead. `integrate git-hooks install` and the `template` commands refuse such a root too, rather than installing hooks that would refuse every commit; `check --staged` refuses one in the index the same way, under any case spelling
of its name, since a case-insensitive disk reads `Loadout.toml` as the manifest; a commit that
removes one is not refused for HEAD still holding it. Registering is the
trust decision: `loadout init global <path>` registers a folder, and there is no other way to
render one.

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
| `--harness NAME` | `init project`, `init global` | Select a harness: `claude`, `codex`, `droid`, `opencode` or `pi`; repeat for several |
| `--root PATH` | `init project` | Project directory to initialise |
| `DIR` (positional) | `init global` | Existing global source, or directory in which to scaffold a new one |

Scope is a subcommand: `init project` for a repository, `init global` for machine-wide
configuration. `init global [DIR]` takes the directory as an optional argument, like
`git init [dir]`, and defaults to the current directory, so a bare `init global` adopts the cwd. `--root` exists only on `init project`; `--registration keep|replace` only on `init global`. At the registered global source that also holds a project, `init project` previews, compares and writes the project's outputs alone; the global ones stay for `sync`. Keeping another registration while init sets up a new source exits 3 before writing anything: the first sync renders global scope, which only the registered source may.
Init either converts or puts everything back. It backs up every path it will touch first and
deletes that backup when it succeeds. If applying or the first render fails, Ctrl-C included,
it puts every path back from the backup, deletes it and exits 1, or 130 after Ctrl-C. After such a failure, and when
the plan cannot complete (exit 2), it recommends doing the conversion with the bundled `/loadout`
skill. At a terminal it asks whether to copy the skill into the user skills folder of each
harness being converted; without one, or under `--json`, it prints the command instead. A refusal
that only needs `--harness`, a scope or `--registration` names that option and not the skill.
An init killed part way leaves its backup, and the next `init` puts it back before anything else:
at a terminal it asks first, and without one it exits 2 with `"status": "interrupted"`.

`loadout integrate skill copy --harness NAME` (repeatable) copies the bundled skill as it is into
`~/.claude/skills/loadout` for Claude, `~/.factory/skills/loadout` for Droid and
`~/.agents/skills/loadout` for Codex, OpenCode and Pi, following `CLAUDE_CONFIG_DIR` and
`FACTORY_HOME_OVERRIDE`. It is a plain copy: no source holds it and `sync` never touches it. An
identical copy already there is left as it is; one that differs is refused before anything is
copied, exit 3. It then says how to start the skill in each harness.

Under `--json`, `init` prints one JSON object on stdout, whatever happens. It always has
`status` and `exit_code`, the code the command exits with:

| `status` | `exit_code` | When |
| --- | --- | --- |
| `preview` | 0 | `--dry-run`: the plan, with every planned write, `secrets`, and `outputs`, each with its `change`: `identical`, `normalised` (only loadout's banner or formatting differs), `update` or `create` |
| `incomplete` | 2 | the plan needs a choice or a fix; `issues` names each, and unless every issue is a choice an option makes, `recommendation` and `install_command` name the skill and the command that copies it |
| `complete` | 0 | applied; `written` (the source), `rendered` (the generated files), `untracked`, `already_initialized`, `delinked`, `unadopted` and `secrets` |
| `restored` | 1, or 130 | init failed, or Ctrl-C stopped it (130), and put every path back; `error` says why, `restored` names what was put back, and `recommendation` and `install_command` are as for `incomplete` |
| `interrupted` | 2, 1 or 130 | an earlier init was killed part way (2), or this one failed (1) or was stopped with Ctrl-C (130) and could not put everything back; `backup` names the backup and `error` says what to do |
| `error` | 2, 3 or 4 | a usage error (an unknown option or `--harness` name included), a source error, or an unexpected one; `error` holds the message |

Fresh project initialisation uses explicit `--harness` options, then the machine configuration's
`harnesses` default, then discovered project configuration. If none establishes membership, it
requires a selection. Global initialisation accepts `--harness` and automatically records its
resolved harness list as the project default.

Re-running project initialisation on an initialised project reports it as already initialised
and changes nothing; `loadout sync` keeps `.gitignore` covering the generated paths.

## Add a project harness

Add the name to `harnesses` in `loadout/config.toml`, then run `loadout sync`:

```toml
harnesses = ["claude", "opencode", "pi"]
```

The accepted names are `claude`, `codex`, `droid`, `opencode`, and `pi`. `sync` writes the new
harness's outputs and extends `.gitignore` to cover every generated path. Removing a harness from
the list retires its outputs on the next sync and drops their `.gitignore` entries.

## Templates

```sh
loadout template add python --tracked
loadout template add python --vendored
loadout template sync python
loadout template list
```

All four accept `--root PATH` and work on a project. Outside one they exit 3: templates are
applied to projects and authored as `templates/<name>.toml` manifests in your global source.
Bare `template add` in a project lists the templates your sources offer.

| Command | Result |
| --- | --- |
| `add NAME --tracked` | Declare the template; it resolves from a source on every render |
| `add NAME --vendored` | Copy the source into the project and record its content hash |
| `add` (bare) | List every resolvable template and prompt for a name and a mode; without a terminal, a missing name or mode is a usage error (exit 2) |
| `sync NAME` | Update an unchanged vendored copy from the available upstream source |
| `list` | Show declared templates, each one's mode, and where it resolves |

Adding a template that is already declared changes its mode in either direction.

When vendoring or syncing a catalog template would change parts it shares with other declared
templates, the command lists the affected templates and asks before writing. `--yes` (on `add`
and `sync`) applies the change without asking; without a terminal and without `--yes` it exits 2
and changes nothing. Declining exits 0 with "declined; no changes made".

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
loadout integrate skill copy --harness claude --harness codex
```

`install`, `status` and `uninstall` accept `--profile NAME` and `--source NAME`. `--source` names a declared
`[[source]]` from the manifest; it is not a directory.

`install` and `uninstall` accept `--yes` to apply the displayed change without a prompt.
They operate on the global source and synchronise the selected profile.

Installation and removal protect edited skill copies. An unchanged installed copy can be
upgraded or removed; a conflicting local edit is reported for review. A source copy loadout does
not own but that is byte for byte the bundled skill, such as one `copy` made that a later `init`
adopted, shows as `identical copy` and `install` takes it over; a copy that differs stays
`conflicting` and is refused.

`copy` needs no source: it copies the skill into each named harness's user skills folder for a
conversion `init` could not do, as [Initialise](#initialise) describes.

### Git hooks

```sh
loadout integrate git-hooks install --root .
loadout integrate git-hooks install --root . --regenerate
loadout integrate git-hooks status --root .
loadout integrate git-hooks uninstall --root . --yes
```

All three accept `--root`; none takes `--profile`, since each hook resolves the profile when it
runs. `install` also accepts `--dry-run`, and
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
| `0` | Success: nothing to do, or a check that found no differences |
| `1` | Drift, a refused safe update, an init that failed and restored itself, or a restore that kept paths |
| `2` | Invalid command-line usage, unresolved init choices, missing noninteractive approval, or an interrupted init found by the next one |
| `3` | Source, configuration, or resolution error: missing or invalid input, an unsafe destination, or an ownership collision |
| `4` | Unexpected internal error, with a traceback |
| `130` | `init` stopped with Ctrl-C, after putting back every path it changed |

A modified vendored template is a source change. `check` can report it without failing solely
for that reason. A refused `template sync` exits `1`.
