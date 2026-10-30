---
title: CLI reference
description: Loadout commands, scope flags, profile selection, init, template operations, integrations, and exit codes.
---

# CLI reference

Run `loadout --help` or `loadout COMMAND --help` for the installed version's help, and
`loadout --version` for the installed version. Commands default to the current directory. Run
them from the source or repository root, or supply `--root PATH`.

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
loadout check --staged
```

| Option | Commands | Meaning |
| --- | --- | --- |
| `--root PATH` | `sync`, `check` | Directory containing the source/project configuration; a `loadout.toml` there renders only when the directory is this machine's registered global source |
| `--global` | `sync`, `check` | Resolve the source and default profile from this machine's configuration |
| `--profile NAME` | `sync`, `check` | Select a global manifest profile; without it, the registered global source uses the machine's profile and any other root uses `default` |
| `--force` | `sync` | Overwrite generated files protected as modified outside Loadout |
| `--json` | `check` | Emit `status`, `exit_code`, each out-of-sync `path` and any notes as JSON; not with `--staged` |
| `--staged` | `check` | Validate what the Git index would commit, rendered in a temporary tree; this is what the pre-commit hook runs |

`--root` and `--global` are mutually exclusive. `--global` without a machine config exits 3 with
``no machine config at <path>; run `loadout init global` first``.

Every run picks its profile by one rule: `--profile` when given; otherwise the machine config's
`profile` when the root is the registered global source, whether named by `--global`, by `--root`,
by a git hook or by init; otherwise `default`. So a manual sync and a hook-run sync of the global
source render the same profile, and neither retires the other's outputs. A machine config that
cannot be read, a folder in its place included, stops a run at a global source rather than fall
back to `default`, which would retire the profile's outputs; a git hook there stops too, after git
has finished, and says what to run once the config is fixed. A project, hooks included, falls back
to `default`.

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
deleted). Settings a harness saves itself, such as Claude Code's `/model`, are drift too; see
[settings a harness saves itself](../troubleshooting.md#settings-a-harness-saves-itself).
`check --json` reports the same kind per path and exits with the same code as `check`. How `sync`
tells its own output from an edit, what it retires, and what it leaves alone is described in
[what sync protects, retires and reports](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md).
A refused `sync` prints the diff for each file it refuses; when the terminal shortens it, the
whole diff is in `$XDG_STATE_HOME/loadout/sync-abort.diff`. A file holding what looks like a secret
is never diffed: one line names it, so read the file directly.

The registered global
source may also hold a project configuration, and then renders both scopes. Any other root holding
a `loadout.toml` is refused with exit 3 before anything is read or written, its project
configuration included. The refusal names its cause: this machine has no registered source (run
`loadout init global <that folder>`), or another one is registered (add `--registration replace`).
A machine config that does not load is reported as its own error instead. `integrate git-hooks install` and the `template` commands refuse such a root too, rather than installing hooks that would refuse every commit; `check --staged` refuses one in the index the same way, under any case spelling
of its name, since a case-insensitive disk reads `Loadout.toml` as the manifest; a commit that
removes one is not refused for HEAD still holding it. Registering is the
trust decision: `loadout init global <path>` registers a folder, and there is no other way to
render one.

`check --staged` reads the index Git supplies, including `GIT_INDEX_FILE`, and needs git: when it
cannot run git it exits 3 and says so. Its inputs, refusals and timing are in
[Git integration](https://github.com/nielsmadan/loadout/blob/main/docs/reference/git-integration.md).

JSON output (`init --json`, `check --json`) is not covered by a stability promise in 1.0.

Use `--force` only after reviewing the reported output edits and deciding to discard them.
It does not resolve invalid sources or merge a modified vendored template.

## Initialise

```sh
loadout init project --harness claude --harness codex --dry-run
loadout init project --harness claude --harness codex
loadout init project --harness opencode --root /path/to/repository
mkdir -p ~/agent-config && loadout init global ~/agent-config
loadout init global --dry-run --json
```

| Option | Commands | Meaning |
| --- | --- | --- |
| `--harness NAME` | `init project`, `init global` | Select a harness: `claude`, `codex`, `droid`, `opencode` or `pi`; repeat for several |
| `--root PATH` | `init project` | Project directory to initialise |
| `DIR` (positional) | `init global` | Existing directory: a global source, or the directory to hold a new one; default the current directory |
| `--registration keep\|replace` | `init global` | Resolve a conflict with the source this machine already registers |
| `--dry-run` | `init project`, `init global` | Preview without changing the source, Git or any destination; exits 0 |
| `--yes` | `init project`, `init global` | Apply a fully resolved plan without asking |
| `--json` | `init project`, `init global` | Print one JSON object instead of text; see the status table below |

Scope is a subcommand: `init project` for a repository, `init global` for machine-wide
configuration.

**Approval.** Init first shows a read-only preview of the source and output writes, private
exclusions and removals. At a terminal it then asks `Apply this migration? [y/N]`; declining
exits 0 with `declined; no changes made`. Without a terminal, or under `--json`, it needs `--yes`
and otherwise exits 2 with
`init requires --yes after reviewing a --dry-run preview; no changes made`. `--yes` approves resolved operations only: it never chooses a registration or harnesses.

**Harnesses.** Fresh project initialisation uses explicit `--harness` options, then the machine
configuration's `harnesses` default, then discovered project configuration. When none establishes
membership, init asks for the harnesses at a terminal, and otherwise refuses with
`harnesses-required` and names `--harness`. Global initialisation accepts `--harness` and records
its resolved harness list as the project default.

**Global init.** `init global [DIR]` takes the directory as an optional argument, like
`git init [dir]`, and defaults to the current directory, so a bare `init global` adopts the cwd.
The directory must exist; init refuses a missing one with `selected directory must exist`, so
create it first. For a new source, init writes the source into `DIR/loadout/` and registers that
folder in the machine config: `init global ~/agent-config` registers `~/agent-config/loadout`.
Unless `DIR` is already inside a Git repository, init makes `DIR` the source's own repository. The
source admits every category, under the source name `migrated`. Init creates no commit and prints
the command that makes the first one.
Choose a dedicated directory; a new source repository at HOME or the filesystem root is refused.
An existing Loadout manifest is recognized as the source and left intact; repeat init reports no
migration while refreshing the saved harness default.

Global init discovers existing configuration in live harness roots and the selected directory.
Under a harness root it reads only the paths loadout manages and leaves everything else alone
unread, so runtime state, `.DS_Store` files and notes never stop it; the
[list](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#what-discovery-reads)
is per harness. Init always reads the live deployment, never a dotfiles copy of it in the selected
directory, and has no option to point it elsewhere. A `~/.claude` symlinked into a dotfiles repo,
or one made of per-file links, is adopted by de-linking it: loadout's outputs become real files,
everything else links back into the repo, and the repo is left unchanged. Init names each link it
replaced and which later additions to the repo will not appear; see
[symlinked deployments](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#symlinked-deployments).

A conflicting machine registration needs `--registration replace`, or at a terminal an answer to
the prompt. `--registration keep` is offered only for a source that is already initialised:
keeping another registration while init sets up a new source exits 3 before writing anything, since
the first sync renders global scope, which only the registered source may.

**Project init.** `init project` adopts only configuration with a known slice destination. It
extracts permissions, instructions, settings, hooks, plugins, MCP servers and skills into canonical
sources, and each harness's subagents into one source agent per name. Harness-specific command
trees stay where their harness reads them; loadout neither moves nor ignores them. A tracked
`CLAUDE.md` or `AGENTS.md` becomes `loadout/instructions/main.md`, and the generated files init
adds to `.gitignore` are removed from the index, which stages their removal; the closing line names
them. Init never creates a project's repository. How it treats symlinked folders, identical copies
across harnesses, `.claude/settings.local.json` and `.agents/skills` is in the
[migration reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md).
`init project` needs no machine config: it uses `--harness` or discovery, and closes by noting that
global scope is not configured. At the registered global source that also holds a project,
`init project` previews, compares and writes the project's outputs alone; the global ones stay for
`sync`. Re-running project initialisation on an initialised project reports it as already
initialised and changes nothing; it does not reinterpret an existing source.

**What init leaves for you.** A skill or subagent whose copies differ between harnesses, or a
project `CLAUDE.md` that disagrees with `AGENTS.md`, does not stop init: that one item is left in
place and reported for the `/loadout` skill to merge, and everything else is adopted; see
[what init leaves out](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#what-init-leaves-out).
Configuration init cannot carry is refused instead; see
[what init refuses](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#what-init-refuses).
A file holding a secret is adopted as it is: the preview and `--json` (`secrets`) name each source
file that looks like it holds one, so keep the source's remote private or move the secret into an
environment variable; see
[what init keeps private](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#what-init-keeps-private).

**First render.** Init finishes with the first render. It compares what loadout would write with
what is on disk, writes it and says so when the two match, names a file that only gains loadout's
banner or formatting as `Normalise:`, and shows the diff before applying when they differ
otherwise. A `sync` straight after `init` changes nothing. Init records what it wrote, so the first
`sync` has a baseline. When the root is inside a Git repository, init prints the command that
installs the [Git hooks](#git-hooks); it never installs them itself.

**Convert or put everything back.** Unresolved previews exit 2 without mutation. Before its first
write, init backs up every path it will touch to `$XDG_STATE_HOME/loadout/backups/` (default
`~/.local/state`): file contents and modes, symlinks, directories, and the Git index when it
untracks generated files. It deletes that backup when it succeeds. If applying or the first render
fails, Ctrl-C included, it puts every path back from the backup, removes a repository or folder it
created, deletes the backup and exits 1, or 130 after Ctrl-C. If putting back fails too, the backup
stays and init exits the same way. After such a failure, and when the plan cannot complete
(exit 2), it recommends doing the conversion with the bundled `/loadout` skill. At a terminal it
asks whether to copy the skill into the user skills folder of each harness being converted; without
one, or under `--json`, it prints the command instead. A refusal that only needs `--harness`, a
scope or `--registration` names that option and not the skill. An init killed part way leaves its
backup, and the next `init` puts it back before anything else: at a terminal it asks first, puts
the paths back on yes, says to run init again and exits 0, and exits 2 on no; without a terminal it
exits 2 with `"status": "interrupted"`. See
[convert or put everything back](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md#convert-or-put-everything-back).

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
| `error` | 2, 3 or 4 | a usage error (an unknown option or `--harness` name, or a missing `--yes`, included), a source error, or an unexpected one; `error` holds the message |

## Add a project harness

Add the name to `harnesses` in `loadout/config.toml`, then run `loadout sync`:

```toml
harnesses = ["claude", "opencode", "pi"]
```

The accepted names are `claude`, `codex`, `droid`, `opencode`, and `pi`. `sync` writes the new
harness's outputs and extends `.gitignore` to cover every generated path. Removing a harness from
the list retires its outputs on the next sync and drops their `.gitignore` entries. What each
harness generates is in [project setup](../guides/projects.md#what-sync-generates).

## Templates

```sh
loadout template add python --tracked
loadout template add python --vendored
loadout template sync python
loadout template list
```

All four accept `--root PATH` and work on a project. Outside one they exit 3: templates are
applied to projects and authored in your global source, as `templates/<name>.toml` manifests or
`templates/<name>/` directories.
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

`install` vendors the version-matched `loadout` skill into the configured global source as a
normal `skills/loadout/` source copy and syncs, so every configured harness gets it. `install`,
`status` and `uninstall` read the machine config and active profile, and accept `--profile NAME`
to override the profile and `--source NAME`. `--source` names a declared `[[source]]` from the
manifest; it is not a directory. An existing copy is selected using the same declared overrides as
rendering, and `--source` must then identify that active source. When no copy exists and several
declared sources offer skills, choose the installation source with `--source`.

`install` and `uninstall` accept `--yes` to apply the displayed change without a prompt; without a
terminal they need it. They operate on the global source and synchronise the selected profile.

An ownership marker, `.loadout-bundle.toml` inside the source copy, records the installed content
hash; the skill renderer never copies it. Reinstalling refreshes an unchanged older copy. A source
copy you edited, or an unowned skill with the same name that differs from the bundled one, is
reported and left alone, and the command exits 1. A source copy loadout does not own but that is
byte for byte the bundled skill, such as one `copy` made that a later `init` adopted, shows as
`identical copy` and `install` takes it over; `uninstall` refuses it. Install leaves the manifest
as it is: when `[skills.loadout]` has the `render = false` that init wrote, install prints a note
that it can now be removed. A generated output edited outside Loadout stops install's sync with
exit 1, as it stops any sync, and stops uninstall before it removes anything, with exit 3.

`uninstall` removes only the owned source copy and the files rendered from it that sync would
accept as its own (the current render, what was committed, or what sync last wrote); unrelated
files in the destination directory survive. When uninstalling an overriding copy, the command also
removes its `loadout` override entry from the manifest that declares it, preserving other entries,
comments and file mode. The earlier source's skill becomes active again and the command's sync
deploys it. An inherited declaration is updated in its declaring parent, affecting every profile
that inherits those sources. Each file is moved aside before it is removed and put back if a later
step fails; one that cannot be put back is left where the error names it.

Invoke the installed skill as `loadout` using the harness's skill syntax. Configuration defaults
to personal rules for the current project; name project scope (`--project`) for committed
repository configuration, or global scope (`--global`) for machine-wide configuration. The skill edits Loadout sources,
applies generic requests to configured harnesses that support them, and runs the matching sync
command. Restart an already-running agent session after first installation so it refreshes its
skill catalog.

`copy` needs no source: it copies the skill into each named harness's user skills folder for a
conversion `init` could not do, as [Initialise](#initialise) describes.

### Git hooks

```sh
loadout integrate git-hooks install --root . --dry-run
loadout integrate git-hooks install --root .
loadout integrate git-hooks install --root . --regenerate
loadout integrate git-hooks status --root .
loadout integrate git-hooks uninstall --root . --yes
```

All three accept `--root`; none takes `--profile`, since each hook resolves the profile when it
runs, the way `sync` does. `install` writes the pre-commit hook, which runs `check --staged`.
`--regenerate` adds the post-checkout and post-merge hooks, which run guarded `sync`, and rewrites
an out-of-date hook loadout wrote. `install --dry-run` prints each hook, its path and what `install`
would do with it, and writes nothing. Hooks are local installation state: a new clone needs
`install` run explicitly.

`install` creates only absent hooks and preserves an existing one, printing the lines to add to it.
In a repository with linked worktrees, the hooks directory is shared by every worktree; `install`
writes it once, from any of them, says it is shared, and the hooks run in each. A hooks directory
outside the repository, such as a global `core.hooksPath`, may serve other repositories, so
`install` writes nothing there and exits 0, printing each block to add by hand, guarded so it runs
only in this repository and its worktrees. In a linked worktree of the registered global source,
post-checkout and post-merge sync nothing and say so; sync from the registered source itself. On a
branch with no loadout config, each hook prints one line saying there is nothing to check or sync,
and exits 0.

`status` prints the effective hooks directory, whether it is outside the repository or shared with
other worktrees, and each hook's state: not installed, installed by loadout, installed by loadout
but out of date, or present and not loadout's.

`uninstall` removes only hooks loadout wrote, identified by the marker on the second line of
the generated script rather than by the command they invoke. A hook without that marker is
never touched, whoever wrote it. A hook that loadout wrote for a different source in the same
repository is left alone too. In a shared hooks directory it says so before removing; a directory
outside the repository is refused. `--yes` skips the confirmation prompt, and is required without
a terminal. See [Git integration](https://github.com/nielsmadan/loadout/blob/main/docs/reference/git-integration.md)
for alternate indexes, worktrees, profiles and what happens when a post-checkout or post-merge
sync fails.

### Shell completions

```sh
loadout integrate completions install
loadout integrate completions install --shell fish
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
| `1` | Drift, a refused safe update, or an init that failed, whether or not it could put everything back |
| `2` | Invalid command-line usage, unresolved init choices, missing noninteractive approval, or an interrupted init found by the next one |
| `3` | Source, configuration, or resolution error: missing or invalid input, an unsafe destination, or an ownership collision |
| `4` | Unexpected internal error, with a traceback |
| `130` | `init` stopped with Ctrl-C, after putting back every path it changed or trying to |

A modified vendored template is a source change. `check` can report it without failing solely
for that reason. `template sync` on a directory template you edited prints the diff, changes
nothing and exits `1`.
